import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue, TypeAdapter

from awesome_agent.agent import AgentRuntimeContext, AgentState, new_agent_state
from awesome_agent.application.context import frozen_context_snapshot_is_valid
from awesome_agent.application.events import ApplicationEventProjector
from awesome_agent.application.operations import OperationController
from awesome_agent.application.turns import (
    ContextSnapshotValidator,
    RecoveryStatus,
    TurnCoordinator,
    TurnExecutionFailed,
)
from awesome_agent.config import BudgetConfig, TurnConfig
from awesome_agent.context import calculate_context_budget, estimate_messages
from awesome_agent.conversation import ConversationService, ThreadView, Turn, TurnStatus
from awesome_agent.core.events import (
    CollectingEventSink,
    EventEmitter,
    EventEnvelope,
    EventType,
)
from awesome_agent.memory import MemoryDocumentInvalid
from awesome_agent.modeling import (
    AssistantMessage,
    ModelMessage,
    SystemMessage,
    UserMessage,
)
from awesome_agent.storage.checkpoints import CheckpointCorrupt
from awesome_agent.storage.conversations import SQLiteConversationRepositories


class FakeGraph:
    def __init__(self, result: AgentState, gate: asyncio.Event | None = None) -> None:
        self.result = result
        self.gate = gate
        self.inputs: list[AgentState] = []
        self.configs: list[dict[str, Any]] = []

    async def ainvoke(
        self,
        state: AgentState | None,
        config: dict[str, Any],
        *,
        context: AgentRuntimeContext,
    ) -> AgentState:
        del context
        assert state is not None
        self.inputs.append(state)
        self.configs.append(config)
        if self.gate is not None:
            await self.gate.wait()
        return self.result


class ResumeGraph(FakeGraph):
    def __init__(self, result: AgentState) -> None:
        super().__init__(result)
        self.resume_inputs: list[AgentState | None] = []

    async def ainvoke(
        self,
        state: AgentState | None,
        config: dict[str, Any],
        *,
        context: AgentRuntimeContext,
    ) -> AgentState:
        del config, context
        self.resume_inputs.append(state)
        return self.result


class RaisingGraph(FakeGraph):
    async def ainvoke(
        self,
        state: AgentState | None,
        config: dict[str, Any],
        *,
        context: AgentRuntimeContext,
    ) -> AgentState:
        del state, config, context
        raise RuntimeError("graph failed")


class FakeCheckpoints:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.state: AgentState | None = None

    async def exists(self, turn_id: str) -> bool:
        return turn_id not in self.deleted

    async def latest_state(self, turn_id: str) -> AgentState | None:
        del turn_id
        return self.state

    async def delete(self, turn_id: str) -> None:
        self.deleted.append(turn_id)


class MappingCheckpoints:
    def __init__(self, states: dict[str, AgentState]) -> None:
        self.states = states
        self.deleted: list[str] = []

    async def exists(self, turn_id: str) -> bool:
        return turn_id in self.states and turn_id not in self.deleted

    async def latest_state(self, turn_id: str) -> AgentState | None:
        return self.states.get(turn_id)

    async def delete(self, turn_id: str) -> None:
        self.deleted.append(turn_id)


class BlockingOperationStartedSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()

    async def emit(self, event: EventEnvelope) -> None:
        if event.event_type is EventType.OPERATION_STARTED:
            self.entered.set()
            await asyncio.Event().wait()
        await super().emit(event)


class BlockingTurnStartedSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()

    async def emit(self, event: EventEnvelope) -> None:
        if event.event_type is EventType.TURN_STARTED:
            self.entered.set()
            await asyncio.Event().wait()
        await super().emit(event)


class BlockingTurnCompletedSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def emit(self, event: EventEnvelope) -> None:
        if event.event_type is EventType.TURN_COMPLETED:
            self.entered.set()
            await self.release.wait()
        await super().emit(event)


class BlockingTurnFailedSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def emit(self, event: EventEnvelope) -> None:
        if event.event_type is EventType.TURN_FAILED:
            self.entered.set()
            await self.release.wait()
        await super().emit(event)


class FailOnTerminalSink(CollectingEventSink):
    def __init__(self, event_type: EventType) -> None:
        super().__init__()
        self._event_type = event_type

    async def emit(self, event: EventEnvelope) -> None:
        if event.event_type is self._event_type:
            raise BrokenPipeError("protocol output closed")
        await super().emit(event)


def _config() -> TurnConfig:
    return TurnConfig(
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        budgets=BudgetConfig(),
    )


def _result(*, final_answer: str | None, reason: str) -> AgentState:
    state = new_agent_state(
        thread_id="placeholder",
        turn_id="placeholder",
        workspace_key="workspace_1",
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        thinking_enabled=False,
    )
    state["final_answer"] = final_answer
    state["termination_reason"] = reason
    state["usage"] = {"input_tokens": 10, "output_tokens": 3}
    state["model_calls"] = 1
    return state


def _freeze_context(
    state: AgentState,
    *,
    turn: Turn,
    conversation: ConversationService,
) -> None:
    content = "inspect"
    state["turn_id"] = turn.id
    state["thread_id"] = turn.thread_id
    state["provider"] = turn.provider
    state["model"] = turn.model
    state["thinking_enabled"] = turn.thinking_enabled
    product = SystemMessage(
        content="[product_instructions:product]\nproduct policy",
    )
    current = UserMessage(
        content=f"[current_input:{turn.user_entry_id}]\n{content}",
    )
    state["messages"] = [
        product.model_dump(mode="json"),
        current.model_dump(mode="json"),
    ]
    state["context_manifest"] = [
        {
            "kind": "product_instructions",
            "source_id": "product",
            "order": 0,
            "estimated_tokens": estimate_messages((product,)),
            "truncated": False,
            "content_hash": hashlib.sha256(b"product policy").hexdigest(),
            "covered_sequence_start": None,
            "covered_sequence_end": None,
        },
        {
            "kind": "current_input",
            "source_id": turn.user_entry_id,
            "order": 1,
            "estimated_tokens": estimate_messages((current,)),
            "truncated": False,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "covered_sequence_start": None,
            "covered_sequence_end": None,
        },
    ]
    state["context_estimated_tokens"] = estimate_messages((product, current))
    state["context_effective_limit"] = calculate_context_budget(
        turn.budgets.total_context_tokens,
        turn.budgets.total_context_tokens,
    ).effective_input_limit
    conversation.store_context_manifest(turn.id, tuple(state["context_manifest"]))


def _manifest_with_optional_direct_command(
    manifest: tuple[dict[str, JsonValue], ...],
    *,
    entry_id: str,
) -> tuple[dict[str, JsonValue], ...]:
    content = "UNTRUSTED direct command result"
    message = AssistantMessage(content=f"[direct_command:{entry_id}]\n{content}")
    return (
        manifest[0],
        {
            "kind": "direct_command",
            "source_id": entry_id,
            "order": 1,
            "estimated_tokens": estimate_messages((message,)),
            "truncated": False,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "covered_sequence_start": 1,
            "covered_sequence_end": 1,
        },
        {**manifest[1], "order": 2},
    )


def _coordinator(
    tmp_path: Path,
    graph: FakeGraph,
    *,
    turn_input_preparer: Callable[[Turn, str], None] | None = None,
    turn_extension_preparer: Callable[[], Awaitable[None]] | None = None,
    event_sink: CollectingEventSink | None = None,
    context_snapshot_validator: ContextSnapshotValidator = (
        frozen_context_snapshot_is_valid
    ),
) -> tuple[
    TurnCoordinator,
    ConversationService,
    CollectingEventSink,
    FakeCheckpoints,
    SQLiteConversationRepositories,
    str,
]:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread("workspace_1")
    sink = event_sink or CollectingEventSink()
    emitter = EventEmitter(
        session_id="session_1",
        workspace_key="workspace_1",
        sink=sink,
    )
    checkpoints = FakeCheckpoints()

    def runtime_factory(
        turn: object,
        operation_id: str,
        projector: ApplicationEventProjector,
    ) -> AgentRuntimeContext:
        del turn, operation_id, projector
        return cast(AgentRuntimeContext, object())

    async def default_extension_preparer() -> None:
        return None

    coordinator = TurnCoordinator(
        workspace_key="workspace_1",
        conversation=conversation,
        config_resolver=lambda thread: TurnConfig(
            provider=(
                "kimi"
                if thread.current_model is not None
                and thread.current_model.startswith("kimi/")
                else "deepseek"
            ),
            model=thread.current_model or "deepseek/deepseek-v4-flash",
            thinking_enabled=thread.thinking_enabled,
            budgets=BudgetConfig(),
        ),
        graph=cast(Any, graph),
        runtime_context_factory=runtime_factory,
        operations=OperationController(emitter),
        emitter=emitter,
        checkpoints=checkpoints,
        seal_changes=lambda turn_id: None,
        turn_input_preparer=(turn_input_preparer or (lambda turn, content: None)),
        turn_extension_preparer=(turn_extension_preparer or default_extension_preparer),
        context_snapshot_validator=context_snapshot_validator,
    )
    return coordinator, conversation, sink, checkpoints, repositories, thread.id


@pytest.mark.asyncio
async def test_shutdown_during_operation_start_fails_persisted_turn_and_releases_lease(
    tmp_path: Path,
) -> None:
    sink = BlockingOperationStartedSink()
    coordinator, conversation, _, _, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="unused", reason="completed")),
        event_sink=sink,
    )
    submission = asyncio.create_task(
        coordinator.submit_turn(
            thread_id,
            "inspect",
            client_message_id="client_shutdown_start",
        )
    )
    await sink.entered.wait()

    await asyncio.wait_for(coordinator._operations.shutdown(), timeout=1)

    with pytest.raises(asyncio.CancelledError):
        await submission
    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.FAILED
    assert turn.error_code == "operation_start_failed"


@pytest.mark.asyncio
async def test_submit_rejects_thread_from_another_workspace_without_mutation(
    tmp_path: Path,
) -> None:
    coordinator, conversation, _, _, _, _ = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="must not run", reason="completed")),
    )
    foreign = conversation.create_thread("workspace_2")

    with pytest.raises(TurnExecutionFailed, match="thread_workspace_mismatch"):
        await coordinator.submit_turn(
            foreign.id,
            "inspect",
            client_message_id="client_foreign_submit",
        )

    assert conversation.read_thread(foreign.id).turns == ()
    assert coordinator.active_operation_id is None


@pytest.mark.asyncio
async def test_resume_rejects_thread_from_another_workspace_without_mutation(
    tmp_path: Path,
) -> None:
    coordinator, conversation, _, checkpoints, _, _ = _coordinator(
        tmp_path,
        ResumeGraph(_result(final_answer="must not run", reason="completed")),
    )
    foreign = conversation.create_thread("workspace_2")
    turn = conversation.begin_turn(
        foreign.id,
        "inspect",
        _config(),
        client_message_id="client_foreign_resume",
    )

    with pytest.raises(TurnExecutionFailed, match="thread_workspace_mismatch"):
        await coordinator.resume_unfinished(foreign.id)

    recovered = conversation.read_thread(foreign.id).turns[0]
    assert recovered.status is TurnStatus.IN_PROGRESS
    assert checkpoints.deleted == []
    assert turn.id == recovered.id
    assert coordinator.active_operation_id is None


@pytest.mark.asyncio
async def test_turn_started_delivery_failure_terminalizes_before_graph_execution(
    tmp_path: Path,
) -> None:
    graph = FakeGraph(_result(final_answer="must not run", reason="completed"))
    sink = FailOnTerminalSink(EventType.TURN_STARTED)
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        graph,
        event_sink=sink,
    )

    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_turn_started_failure",
    )
    with pytest.raises(BrokenPipeError, match="protocol output closed"):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert (turn.status, turn.error_code) == (
        TurnStatus.FAILED,
        "turn_start_failed",
    )
    assert checkpoints.deleted == [turn.id]
    assert graph.inputs == []


@pytest.mark.asyncio
async def test_cancel_after_durable_turn_commit_is_rejected(
    tmp_path: Path,
) -> None:
    sink = BlockingTurnCompletedSink()
    coordinator, conversation, _, _, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="done", reason="completed")),
        event_sink=sink,
    )
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_cancel_after_commit",
    )
    await sink.entered.wait()

    assert await coordinator.cancel_operation(accepted.operation_id) is False
    sink.release.set()
    await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.COMPLETED
    event_types = [event.event_type for event in sink.events]
    assert event_types.count(EventType.TURN_COMPLETED) == 1
    assert event_types.count(EventType.OPERATION_COMPLETED) == 1
    assert event_types.count(EventType.TURN_CANCELLED) == 0
    assert event_types.count(EventType.OPERATION_CANCELLED) == 0
    assert coordinator.active_operation_id is None


@pytest.mark.asyncio
async def test_cancel_after_durable_turn_failure_is_rejected(
    tmp_path: Path,
) -> None:
    sink = BlockingTurnFailedSink()
    coordinator, conversation, _, _, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer=None, reason="model_failed")),
        event_sink=sink,
    )
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_cancel_after_failure",
    )
    await sink.entered.wait()

    assert await coordinator.cancel_operation(accepted.operation_id) is False
    sink.release.set()
    with pytest.raises(TurnExecutionFailed, match="model_failed"):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert (turn.status, turn.error_code) == (TurnStatus.FAILED, "model_failed")
    event_types = [event.event_type for event in sink.events]
    assert event_types.count(EventType.TURN_FAILED) == 1
    assert event_types.count(EventType.OPERATION_FAILED) == 1
    assert event_types.count(EventType.TURN_CANCELLED) == 0
    assert event_types.count(EventType.OPERATION_CANCELLED) == 0
    assert coordinator.active_operation_id is None


@pytest.mark.asyncio
async def test_completed_turn_write_error_reconciles_exact_durable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="done", reason="completed"), gate),
    )
    persist = conversation.complete_turn

    def commit_then_raise(*args: Any, **kwargs: Any) -> Turn:
        persist(*args, **kwargs)
        raise RuntimeError("connection close failed after commit")

    monkeypatch.setattr(conversation, "complete_turn", commit_then_raise)
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_completed_commit_reconcile",
    )
    assert accepted.turn_id is not None
    manifest: tuple[dict[str, JsonValue], ...] = (
        {"kind": "history", "estimated_tokens": 11},
    )
    conversation.store_context_manifest(accepted.turn_id, manifest)
    gate.set()

    await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.COMPLETED
    assert turn.context_manifest == manifest
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events][-2:] == [
        EventType.TURN_COMPLETED,
        EventType.OPERATION_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_completed_turn_write_error_is_not_reconciled_without_exact_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, conversation, sink, _, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="done", reason="completed")),
    )

    def fail_before_commit(*args: Any, **kwargs: Any) -> Turn:
        del args, kwargs
        raise RuntimeError("write failed before commit")

    monkeypatch.setattr(conversation, "complete_turn", fail_before_commit)
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_completed_commit_conflict",
    )

    with pytest.raises(RuntimeError, match="write failed before commit"):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.IN_PROGRESS
    event_types = [event.event_type for event in sink.events]
    assert event_types.count(EventType.TURN_COMPLETED) == 0
    assert event_types.count(EventType.OPERATION_COMPLETED) == 0
    assert event_types.count(EventType.OPERATION_FAILED) == 1


@pytest.mark.asyncio
async def test_failed_turn_write_error_reconciles_exact_durable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer=None, reason="model_failed")),
    )
    persist = conversation.fail_turn

    def commit_then_raise(*args: Any, **kwargs: Any) -> Turn:
        persist(*args, **kwargs)
        raise RuntimeError("connection close failed after commit")

    monkeypatch.setattr(conversation, "fail_turn", commit_then_raise)
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_failed_commit_reconcile",
    )

    with pytest.raises(TurnExecutionFailed, match="model_failed"):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert (turn.status, turn.error_code) == (TurnStatus.FAILED, "model_failed")
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events][-2:] == [
        EventType.TURN_FAILED,
        EventType.OPERATION_FAILED,
    ]


@pytest.mark.asyncio
async def test_cancelled_turn_write_error_reconciles_exact_durable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="unused", reason="completed"), gate),
    )
    persist = conversation.cancel_turn

    def commit_then_raise(*args: Any, **kwargs: Any) -> Turn:
        persist(*args, **kwargs)
        raise RuntimeError("connection close failed after commit")

    monkeypatch.setattr(conversation, "cancel_turn", commit_then_raise)
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_cancelled_commit_reconcile",
    )

    assert await coordinator.cancel_operation(accepted.operation_id) is True
    with pytest.raises(asyncio.CancelledError):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.CANCELLED
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events][-2:] == [
        EventType.TURN_CANCELLED,
        EventType.OPERATION_CANCELLED,
    ]


@pytest.mark.asyncio
async def test_turn_prepares_extensions_inside_operation_and_cleans_up_cancel(
    tmp_path: Path,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def prepare_extensions() -> None:
        entered.set()
        await release.wait()

    graph = FakeGraph(_result(final_answer="unreachable", reason="completed"))
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        graph,
        turn_extension_preparer=prepare_extensions,
    )

    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_extensions",
    )
    await entered.wait()
    assert graph.inputs == []
    assert await coordinator.cancel_operation(accepted.operation_id) is True
    with pytest.raises(asyncio.CancelledError):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.CANCELLED
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events] == [
        EventType.OPERATION_STARTED,
        EventType.TURN_STARTED,
        EventType.TURN_CANCELLED,
        EventType.OPERATION_CANCELLED,
    ]


@pytest.mark.asyncio
async def test_submit_cancelled_while_turn_started_is_blocked_is_terminalized_once(
    tmp_path: Path,
) -> None:
    sink = BlockingTurnStartedSink()
    graph = FakeGraph(_result(final_answer="must not run", reason="completed"))
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        graph,
        event_sink=sink,
    )
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_cancel_blocked_turn_start",
    )
    await sink.entered.wait()

    assert await coordinator.cancel_operation(accepted.operation_id) is True
    with pytest.raises(asyncio.CancelledError):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.CANCELLED
    assert checkpoints.deleted == [accepted.turn_id]
    assert graph.inputs == []
    assert [event.event_type for event in sink.events] == [
        EventType.OPERATION_STARTED,
        EventType.OPERATION_CANCELLED,
    ]


@pytest.mark.asyncio
async def test_resume_cancelled_while_turn_started_is_blocked_is_terminalized_once(
    tmp_path: Path,
) -> None:
    sink = BlockingTurnStartedSink()
    graph = ResumeGraph(_result(final_answer="must not run", reason="completed"))
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        graph,
        event_sink=sink,
    )
    turn = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id="client_resume_cancel_blocked_turn_start",
    )
    checkpoints.state = _result(final_answer=None, reason="waiting")
    _freeze_context(checkpoints.state, turn=turn, conversation=conversation)
    accepted = await coordinator.resume_unfinished(thread_id)
    await sink.entered.wait()

    assert await coordinator.cancel_operation(accepted.operation_id) is True
    with pytest.raises(asyncio.CancelledError):
        await coordinator.wait(accepted.operation_id)

    recovered = conversation.read_thread(thread_id).turns[0]
    assert recovered.status is TurnStatus.CANCELLED
    assert checkpoints.deleted == [turn.id]
    assert graph.resume_inputs == []
    assert [event.event_type for event in sink.events] == [
        EventType.OPERATION_STARTED,
        EventType.OPERATION_CANCELLED,
    ]


@pytest.mark.asyncio
async def test_submit_turn_freezes_config_commits_then_emits_and_cleans_checkpoint(
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    graph = FakeGraph(_result(final_answer="done", reason="completed"), gate)
    coordinator, conversation, sink, checkpoints, repositories, thread_id = (
        _coordinator(tmp_path, graph)
    )

    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_1",
    )
    assert accepted.thread_id == thread_id
    assert accepted.turn_id is not None
    assert accepted.operation_id
    assert accepted.client_message_id == "client_1"
    assert coordinator.active_operation_id == accepted.operation_id

    view = conversation.read_thread(thread_id)
    repositories.threads.update(
        view.thread.model_copy(
            update={
                "current_model": "kimi/kimi-k2.6",
                "thinking_enabled": False,
            }
        )
    )
    gate.set()
    await coordinator.wait(accepted.operation_id)

    completed = conversation.read_thread(thread_id)
    assert completed.turns[0].status is TurnStatus.COMPLETED
    assert completed.entries[-1].content == "done"
    assert completed.entries[0].client_message_id == "client_1"
    assert {event.client_message_id for event in sink.events} == {"client_1"}
    assert graph.inputs[0]["provider"] == "deepseek"
    assert graph.inputs[0]["thinking_enabled"] is True
    assert graph.configs[0]["configurable"]["thread_id"] == accepted.turn_id
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events] == [
        EventType.OPERATION_STARTED,
        EventType.TURN_STARTED,
        EventType.TURN_COMPLETED,
        EventType.OPERATION_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_model_failure_persists_failed_turn_and_failed_operation(
    tmp_path: Path,
) -> None:
    graph = FakeGraph(_result(final_answer=None, reason="model_authentication"))
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path, graph
    )

    accepted = await coordinator.submit_turn(
        thread_id, "inspect", client_message_id="client_1"
    )
    with pytest.raises(TurnExecutionFailed, match="model_authentication"):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.FAILED
    assert turn.error_code == "model_authentication"
    assert turn.usage.input_tokens == 10
    assert turn.usage.output_tokens == 3
    assert turn.usage.model_calls == 1
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events][-2:] == [
        EventType.TURN_FAILED,
        EventType.OPERATION_FAILED,
    ]


@pytest.mark.asyncio
async def test_preprocessing_failure_terminalizes_turn_and_allows_next_turn(
    tmp_path: Path,
) -> None:
    attempts = 0

    def prepare_input(turn: Turn, content: str) -> None:
        nonlocal attempts
        del turn, content
        attempts += 1
        if attempts == 1:
            raise RuntimeError("preprocessing fault")

    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="done", reason="completed")),
        turn_input_preparer=prepare_input,
    )

    failed = await coordinator.submit_turn(
        thread_id,
        "first",
        client_message_id="client_preprocessing_failure",
    )
    with pytest.raises(RuntimeError, match="preprocessing fault"):
        await coordinator.wait(failed.operation_id)

    first = conversation.read_thread(thread_id).turns[0]
    assert first.status is TurnStatus.FAILED
    assert first.error_code == "turn_preparation_failed"
    assert checkpoints.deleted == [failed.turn_id]
    assert coordinator.active_operation_id is None

    succeeded = await coordinator.submit_turn(
        thread_id,
        "second",
        client_message_id="client_after_preprocessing_failure",
    )
    await coordinator.wait(succeeded.operation_id)

    turns = conversation.read_thread(thread_id).turns
    assert [turn.status for turn in turns] == [
        TurnStatus.FAILED,
        TurnStatus.COMPLETED,
    ]
    event_types = [event.event_type for event in sink.events]
    assert event_types.count(EventType.TURN_FAILED) == 1
    assert event_types.count(EventType.OPERATION_FAILED) == 1
    assert event_types.count(EventType.TURN_COMPLETED) == 1
    assert event_types.count(EventType.OPERATION_COMPLETED) == 1


@pytest.mark.asyncio
async def test_invalid_local_memory_preparation_terminalizes_the_created_turn(
    tmp_path: Path,
) -> None:
    def prepare_input(turn: Turn, content: str) -> None:
        del turn, content
        raise MemoryDocumentInvalid("memory_document_invalid")

    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="must not run", reason="completed")),
        turn_input_preparer=prepare_input,
    )

    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect memory",
        client_message_id="client_invalid_local_memory",
    )
    with pytest.raises(MemoryDocumentInvalid, match="memory_document_invalid"):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.FAILED
    assert turn.error_code == "turn_preparation_failed"
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events][-2:] == [
        EventType.TURN_FAILED,
        EventType.OPERATION_FAILED,
    ]


@pytest.mark.asyncio
async def test_preprocessing_cancellation_persists_one_cancelled_terminal(
    tmp_path: Path,
) -> None:
    attempts = 0

    def prepare_input(turn: Turn, content: str) -> None:
        nonlocal attempts
        del turn, content
        attempts += 1
        if attempts == 1:
            raise asyncio.CancelledError

    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="done", reason="completed")),
        turn_input_preparer=prepare_input,
    )

    cancelled = await coordinator.submit_turn(
        thread_id,
        "first",
        client_message_id="client_preprocessing_cancelled",
    )
    with pytest.raises(asyncio.CancelledError):
        await coordinator.wait(cancelled.operation_id)

    first = conversation.read_thread(thread_id).turns[0]
    assert first.status is TurnStatus.CANCELLED
    assert checkpoints.deleted == [cancelled.turn_id]
    assert coordinator.active_operation_id is None

    succeeded = await coordinator.submit_turn(
        thread_id,
        "second",
        client_message_id="client_after_preprocessing_cancelled",
    )
    await coordinator.wait(succeeded.operation_id)

    event_types = [event.event_type for event in sink.events]
    assert event_types.count(EventType.TURN_CANCELLED) == 1
    assert event_types.count(EventType.OPERATION_CANCELLED) == 1
    assert event_types.count(EventType.TURN_FAILED) == 0
    assert event_types.count(EventType.OPERATION_FAILED) == 0


@pytest.mark.asyncio
async def test_graph_exception_persists_last_stable_checkpoint_facts(
    tmp_path: Path,
) -> None:
    graph = RaisingGraph(_result(final_answer=None, reason="waiting"))
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path, graph
    )
    observed = _result(final_answer=None, reason="waiting")
    observed["tool_calls"] = 2
    observed["context_manifest"] = [{"kind": "tool_result", "estimated_tokens": 7}]
    checkpoints.state = observed

    accepted = await coordinator.submit_turn(
        thread_id, "inspect", client_message_id="client_1"
    )
    with pytest.raises(RuntimeError, match="graph failed"):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.FAILED
    assert turn.error_code == "agent_execution_failed"
    assert turn.usage.tool_calls == 2
    assert turn.context_manifest == ({"kind": "tool_result", "estimated_tokens": 7},)


@pytest.mark.asyncio
async def test_graph_exception_preserves_primary_error_when_fact_read_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        RaisingGraph(_result(final_answer=None, reason="waiting")),
    )

    async def fail_latest_state(turn_id: str) -> AgentState | None:
        del turn_id
        raise RuntimeError("checkpoint read failed")

    monkeypatch.setattr(checkpoints, "latest_state", fail_latest_state)
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_graph_fact_failure",
    )

    with pytest.raises(RuntimeError, match="graph failed"):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.FAILED
    assert turn.error_code == "agent_execution_failed"
    assert turn.usage.tool_calls == 0
    assert turn.context_manifest == ()
    assert checkpoints.deleted == [accepted.turn_id]
    event_types = [event.event_type for event in sink.events]
    assert event_types.count(EventType.TURN_FAILED) == 1
    assert event_types.count(EventType.OPERATION_FAILED) == 1


@pytest.mark.asyncio
async def test_graph_exception_commits_before_bounded_fact_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        RaisingGraph(_result(final_answer=None, reason="waiting")),
    )
    facts_entered = asyncio.Event()
    release_facts = asyncio.Event()

    async def block_latest_state(turn_id: str) -> AgentState | None:
        del turn_id
        facts_entered.set()
        await release_facts.wait()
        return None

    monkeypatch.setattr(checkpoints, "latest_state", block_latest_state)
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_graph_fact_cancel",
    )
    await facts_entered.wait()

    assert await coordinator.cancel_operation(accepted.operation_id) is False
    release_facts.set()
    with pytest.raises(RuntimeError, match="graph failed"):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.FAILED
    assert turn.error_code == "agent_execution_failed"
    assert checkpoints.deleted == [accepted.turn_id]
    event_types = [event.event_type for event in sink.events]
    assert event_types.count(EventType.TURN_FAILED) == 1
    assert event_types.count(EventType.OPERATION_FAILED) == 1
    assert event_types.count(EventType.TURN_CANCELLED) == 0
    assert event_types.count(EventType.OPERATION_CANCELLED) == 0


@pytest.mark.asyncio
async def test_agent_state_construction_failure_terminalizes_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="unused", reason="completed")),
    )

    def fail_state(**kwargs: object) -> AgentState:
        del kwargs
        raise ValueError("invalid initial state")

    monkeypatch.setattr("awesome_agent.application.turns.new_agent_state", fail_state)
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_invalid_initial_state",
    )

    with pytest.raises(ValueError, match="invalid initial state"):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.FAILED
    assert turn.error_code == "agent_initialization_failed"
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events][-2:] == [
        EventType.TURN_FAILED,
        EventType.OPERATION_FAILED,
    ]


@pytest.mark.asyncio
async def test_invalid_graph_result_terminalizes_turn(
    tmp_path: Path,
) -> None:
    graph = FakeGraph(cast(AgentState, {}))
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        graph,
    )
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_invalid_graph_result",
    )

    with pytest.raises(KeyError):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.FAILED
    assert turn.error_code == "agent_execution_failed"
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events][-2:] == [
        EventType.TURN_FAILED,
        EventType.OPERATION_FAILED,
    ]


@pytest.mark.asyncio
async def test_graph_result_normalization_cancellation_terminalizes_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="unused", reason="completed")),
    )

    def cancel_normalization(state: AgentState) -> object:
        del state
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "awesome_agent.application.turns.observed_turn_facts",
        cancel_normalization,
    )
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_result_normalization_cancelled",
    )

    with pytest.raises(asyncio.CancelledError):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.CANCELLED
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events][-2:] == [
        EventType.TURN_CANCELLED,
        EventType.OPERATION_CANCELLED,
    ]


@pytest.mark.asyncio
async def test_runtime_context_cancellation_terminalizes_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="unused", reason="completed")),
    )

    def cancel_runtime(*args: object, **kwargs: object) -> AgentRuntimeContext:
        del args, kwargs
        raise asyncio.CancelledError

    monkeypatch.setattr(coordinator, "_runtime_context_factory", cancel_runtime)
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_runtime_cancelled",
    )

    with pytest.raises(asyncio.CancelledError):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.CANCELLED
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events][-2:] == [
        EventType.TURN_CANCELLED,
        EventType.OPERATION_CANCELLED,
    ]


@pytest.mark.asyncio
async def test_cancel_persists_cancelled_turn_before_operation_terminal(
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    graph = FakeGraph(_result(final_answer="unused", reason="completed"), gate)
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path, graph
    )
    accepted = await coordinator.submit_turn(
        thread_id, "inspect", client_message_id="client_1"
    )
    observed = _result(final_answer=None, reason="waiting")
    observed["context_manifest"] = [{"kind": "history", "estimated_tokens": 13}]
    checkpoints.state = observed

    assert await coordinator.cancel_operation(accepted.operation_id) is True
    with pytest.raises(asyncio.CancelledError):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.CANCELLED
    assert turn.usage.input_tokens == 10
    assert turn.usage.output_tokens == 3
    assert turn.context_manifest == ({"kind": "history", "estimated_tokens": 13},)
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events][-2:] == [
        EventType.TURN_CANCELLED,
        EventType.OPERATION_CANCELLED,
    ]


@pytest.mark.asyncio
async def test_cancel_falls_back_when_checkpoint_reader_self_cancels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="unused", reason="completed"), gate),
    )

    async def cancel_latest_state(turn_id: str) -> AgentState | None:
        del turn_id
        raise asyncio.CancelledError

    monkeypatch.setattr(checkpoints, "latest_state", cancel_latest_state)
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_checkpoint_self_cancel",
    )

    assert await coordinator.cancel_operation(accepted.operation_id) is True
    with pytest.raises(asyncio.CancelledError):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.CANCELLED
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events][-2:] == [
        EventType.TURN_CANCELLED,
        EventType.OPERATION_CANCELLED,
    ]


@pytest.mark.asyncio
async def test_cancel_preserves_cancelled_error_when_terminal_delivery_fails(
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    sink = FailOnTerminalSink(EventType.TURN_CANCELLED)
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="unused", reason="completed"), gate),
        event_sink=sink,
    )
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_cancel_transport_failure",
    )

    assert await coordinator.cancel_operation(accepted.operation_id) is True
    with pytest.raises(asyncio.CancelledError):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.CANCELLED
    assert checkpoints.deleted == [accepted.turn_id]


@pytest.mark.asyncio
async def test_cancel_preserves_cancelled_error_when_checkpoint_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="unused", reason="completed"), gate),
    )

    async def fail_delete(turn_id: str) -> None:
        del turn_id
        raise RuntimeError("checkpoint cleanup failed")

    monkeypatch.setattr(checkpoints, "delete", fail_delete)
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_cancel_cleanup_failure",
    )

    assert await coordinator.cancel_operation(accepted.operation_id) is True
    with pytest.raises(asyncio.CancelledError):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_preserves_checkpoint_when_terminal_state_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="unused", reason="completed"), gate),
    )

    def fail_cancel_turn(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("cancel state write failed")

    monkeypatch.setattr(conversation, "cancel_turn", fail_cancel_turn)
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_cancel_state_failure",
    )

    assert await coordinator.cancel_operation(accepted.operation_id) is True
    with pytest.raises(asyncio.CancelledError):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.IN_PROGRESS
    assert checkpoints.deleted == []


@pytest.mark.asyncio
async def test_cancel_then_shutdown_finishes_cancelled_turn_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="unused", reason="completed"), gate),
    )
    observed = _result(final_answer=None, reason="waiting")
    observed["tool_calls"] = 2
    observed["context_manifest"] = [{"kind": "tool_result", "estimated_tokens": 17}]
    checkpoints.state = observed
    facts_entered = asyncio.Event()
    release_facts = asyncio.Event()
    facts_calls = 0

    async def slow_latest_state(turn_id: str) -> AgentState | None:
        nonlocal facts_calls
        del turn_id
        facts_calls += 1
        facts_entered.set()
        await release_facts.wait()
        return checkpoints.state

    async def fail_delete(turn_id: str) -> None:
        checkpoints.deleted.append(turn_id)
        raise RuntimeError("checkpoint cleanup failed")

    monkeypatch.setattr(checkpoints, "latest_state", slow_latest_state)
    monkeypatch.setattr(checkpoints, "delete", fail_delete)
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_cancel_then_shutdown",
    )

    assert await coordinator.cancel_operation(accepted.operation_id) is True
    await facts_entered.wait()
    shutdown = asyncio.create_task(coordinator._operations.shutdown())
    await asyncio.sleep(0)
    release_facts.set()
    await asyncio.wait_for(shutdown, timeout=1)
    with pytest.raises(asyncio.CancelledError):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.CANCELLED
    assert turn.usage.tool_calls == 2
    assert turn.context_manifest == ({"kind": "tool_result", "estimated_tokens": 17},)
    assert facts_calls == 1
    assert checkpoints.deleted == [accepted.turn_id]
    event_types = [event.event_type for event in sink.events]
    assert event_types.count(EventType.TURN_CANCELLED) == 1
    assert event_types.count(EventType.OPERATION_CANCELLED) == 1


@pytest.mark.asyncio
async def test_cancel_bounds_observed_facts_and_uses_safe_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    coordinator, conversation, sink, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="unused", reason="completed"), gate),
    )
    facts_entered = asyncio.Event()

    async def never_returns(turn_id: str) -> AgentState | None:
        del turn_id
        facts_entered.set()
        await asyncio.Event().wait()
        raise AssertionError("Observed facts lookup escaped cancellation timeout.")

    monkeypatch.setattr(checkpoints, "latest_state", never_returns)
    monkeypatch.setattr(
        "awesome_agent.application.turns._CANCELLATION_FACTS_TIMEOUT_SECONDS",
        0.01,
    )
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_cancel_facts_timeout",
    )

    assert await coordinator.cancel_operation(accepted.operation_id) is True
    await facts_entered.wait()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(coordinator.wait(accepted.operation_id), timeout=1)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.CANCELLED
    assert turn.usage.tool_calls == 0
    assert turn.context_manifest == ()
    assert checkpoints.deleted == [accepted.turn_id]
    event_types = [event.event_type for event in sink.events]
    assert event_types.count(EventType.TURN_CANCELLED) == 1
    assert event_types.count(EventType.OPERATION_CANCELLED) == 1


@pytest.mark.asyncio
async def test_graph_failure_preserves_primary_error_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        RaisingGraph(_result(final_answer=None, reason="waiting")),
    )

    async def fail_delete(turn_id: str) -> None:
        del turn_id
        raise RuntimeError("checkpoint cleanup failed")

    monkeypatch.setattr(checkpoints, "delete", fail_delete)
    accepted = await coordinator.submit_turn(
        thread_id,
        "inspect",
        client_message_id="client_graph_cleanup_failure",
    )

    with pytest.raises(RuntimeError, match="graph failed"):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.FAILED
    assert turn.error_code == "agent_execution_failed"


@pytest.mark.asyncio
async def test_resume_uses_official_checkpoint_position_without_new_user_entry(
    tmp_path: Path,
) -> None:
    graph = ResumeGraph(_result(final_answer="resumed", reason="completed"))
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path, graph
    )
    turn = conversation.begin_turn(
        thread_id, "inspect", _config(), client_message_id="client_1"
    )
    checkpoints.state = _result(final_answer=None, reason="waiting")
    _freeze_context(
        checkpoints.state,
        turn=turn,
        conversation=conversation,
    )

    accepted = await coordinator.resume_unfinished(thread_id)
    await coordinator.wait(accepted.operation_id)

    assert accepted.turn_id == turn.id
    assert graph.resume_inputs == [None]
    view = conversation.read_thread(thread_id)
    assert len(view.entries) == 2
    assert view.entries[-1].content == "resumed"


@pytest.mark.asyncio
async def test_resume_repairs_manifest_committed_ahead_of_older_checkpoint(
    tmp_path: Path,
) -> None:
    graph = ResumeGraph(_result(final_answer="resumed", reason="completed"))
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path, graph
    )
    direct = conversation.append_direct_command(thread_id, "prior output", {})
    turn = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id="client_manifest_checkpoint_gap",
    )
    checkpoints.state = _result(final_answer=None, reason="waiting")
    checkpoints.state["termination_reason"] = None
    _freeze_context(checkpoints.state, turn=turn, conversation=conversation)
    checkpoint_manifest = tuple(checkpoints.state["context_manifest"])
    persisted_ahead = _manifest_with_optional_direct_command(
        checkpoint_manifest,
        entry_id=direct.id,
    )
    conversation.store_context_manifest(turn.id, persisted_ahead)

    accepted = await coordinator.resume_unfinished(thread_id)
    await coordinator.wait(accepted.operation_id)

    recovered = conversation.read_thread(thread_id).turns[0]
    assert recovered.status is TurnStatus.COMPLETED
    assert recovered.context_manifest == checkpoint_manifest
    assert graph.resume_inputs == [None]


@pytest.mark.asyncio
async def test_resume_never_repairs_turn_from_tampered_checkpoint_snapshot(
    tmp_path: Path,
) -> None:
    validator_calls = 0

    def permissive_validator(
        state: AgentState,
        *,
        turn: Turn,
        view: ThreadView,
    ) -> bool:
        nonlocal validator_calls
        del state, turn, view
        validator_calls += 1
        return True

    graph = ResumeGraph(_result(final_answer="must not run", reason="completed"))
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        graph,
        context_snapshot_validator=permissive_validator,
    )
    turn = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id="client_tampered_checkpoint",
    )
    checkpoints.state = _result(final_answer=None, reason="waiting")
    _freeze_context(checkpoints.state, turn=turn, conversation=conversation)
    checkpoint_manifest = tuple(checkpoints.state["context_manifest"])
    persisted_ahead = (
        {
            **checkpoint_manifest[0],
            "source_id": "product-after-checkpoint",
        },
        checkpoint_manifest[1],
    )
    conversation.store_context_manifest(turn.id, persisted_ahead)
    checkpoints.state["context_manifest"][0]["content_hash"] = "0" * 64

    with pytest.raises(TurnExecutionFailed, match="context_snapshot_missing"):
        await coordinator.resume_unfinished(thread_id)

    recovered = conversation.read_thread(thread_id).turns[0]
    assert recovered.status is TurnStatus.FAILED
    assert recovered.context_manifest == persisted_ahead
    assert graph.resume_inputs == []
    assert validator_calls == 0


@pytest.mark.asyncio
async def test_startup_reconciliation_repairs_checkpoint_manifest_gap(
    tmp_path: Path,
) -> None:
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="must not run", reason="completed")),
    )
    direct = conversation.append_direct_command(thread_id, "prior output", {})
    turn = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id="client_startup_manifest_gap",
    )
    checkpoints.state = _result(final_answer=None, reason="waiting")
    checkpoints.state["termination_reason"] = None
    _freeze_context(checkpoints.state, turn=turn, conversation=conversation)
    checkpoint_manifest = tuple(checkpoints.state["context_manifest"])
    persisted_ahead = _manifest_with_optional_direct_command(
        checkpoint_manifest,
        entry_id=direct.id,
    )
    conversation.store_context_manifest(turn.id, persisted_ahead)

    [result] = await coordinator.reconcile_startup()

    assert result.status is RecoveryStatus.RESUMABLE
    recovered = conversation.read_thread(thread_id).turns[0]
    assert recovered.status is TurnStatus.IN_PROGRESS
    assert recovered.context_manifest == checkpoint_manifest
    assert checkpoints.deleted == []


@pytest.mark.asyncio
async def test_startup_reconciliation_restores_empty_manifest_from_checkpoint(
    tmp_path: Path,
) -> None:
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="must not run", reason="completed")),
    )
    turn = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id="client_empty_manifest",
    )
    checkpoints.state = _result(final_answer=None, reason="waiting")
    checkpoints.state["termination_reason"] = None
    _freeze_context(checkpoints.state, turn=turn, conversation=conversation)
    checkpoint_manifest = tuple(checkpoints.state["context_manifest"])
    conversation.compare_and_swap_context_manifest(
        turn.id,
        (),
        expected_context_manifest=checkpoint_manifest,
    )

    [result] = await coordinator.reconcile_startup()

    assert result.status is RecoveryStatus.RESUMABLE
    recovered = conversation.read_thread(thread_id).turns[0]
    assert recovered.status is TurnStatus.IN_PROGRESS
    assert recovered.context_manifest == checkpoint_manifest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("variant", "expected_error"),
    (
        ("current_input", "context_snapshot_missing"),
        ("identity", "checkpoint_corrupt"),
        ("budget", "checkpoint_corrupt"),
        ("role", "context_snapshot_missing"),
        ("hash", "context_snapshot_missing"),
    ),
)
async def test_empty_manifest_never_trusts_unverifiable_checkpoint(
    tmp_path: Path,
    variant: str,
    expected_error: str,
) -> None:
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="must not run", reason="completed")),
    )
    turn = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id=f"client_empty_manifest_{variant}",
    )
    checkpoints.state = _result(final_answer=None, reason="waiting")
    checkpoints.state["termination_reason"] = None
    _freeze_context(checkpoints.state, turn=turn, conversation=conversation)
    checkpoint_manifest = tuple(checkpoints.state["context_manifest"])
    conversation.compare_and_swap_context_manifest(
        turn.id,
        (),
        expected_context_manifest=checkpoint_manifest,
    )
    if variant == "current_input":
        content = "tampered but internally consistent"
        current = UserMessage(
            content=f"[current_input:{turn.user_entry_id}]\n{content}"
        )
        checkpoints.state["messages"][1] = current.model_dump(mode="json")
        checkpoints.state["context_manifest"][1]["content_hash"] = hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()
        checkpoints.state["context_manifest"][1]["estimated_tokens"] = (
            estimate_messages((current,))
        )
        messages: tuple[ModelMessage, ...] = tuple(
            TypeAdapter(ModelMessage).validate_python(item)
            for item in checkpoints.state["messages"]
        )
        checkpoints.state["context_estimated_tokens"] = estimate_messages(messages)
    elif variant == "identity":
        checkpoints.state["turn_id"] = "turn_other"
    elif variant == "budget":
        checkpoints.state["tool_calls"] = -1
    elif variant == "role":
        checkpoints.state["messages"][0]["role"] = "user"
    else:
        checkpoints.state["context_manifest"][0]["content_hash"] = "0" * 64

    [result] = await coordinator.reconcile_startup()

    assert (result.status, result.error_code) == (
        RecoveryStatus.FAILED,
        expected_error,
    )
    recovered = conversation.read_thread(thread_id).turns[0]
    assert recovered.status is TurnStatus.FAILED
    assert recovered.context_manifest == ()


@pytest.mark.asyncio
async def test_startup_manifest_cas_conflict_fails_one_turn_and_continues_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, conversation, _, _, _, thread_id = _coordinator(
        tmp_path,
        FakeGraph(_result(final_answer="must not run", reason="completed")),
    )
    direct = conversation.append_direct_command(thread_id, "prior output", {})
    conflicted = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id="client_manifest_conflict",
    )
    conflicted_state = _result(final_answer=None, reason="waiting")
    conflicted_state["termination_reason"] = None
    _freeze_context(conflicted_state, turn=conflicted, conversation=conversation)
    checkpoint_manifest = tuple(conflicted_state["context_manifest"])
    persisted_ahead = _manifest_with_optional_direct_command(
        checkpoint_manifest,
        entry_id=direct.id,
    )
    conversation.store_context_manifest(conflicted.id, persisted_ahead)

    other_thread = conversation.create_thread("workspace_1", "Other")
    resumable = conversation.begin_turn(
        other_thread.id,
        "inspect",
        _config(),
        client_message_id="client_after_manifest_conflict",
    )
    resumable_state = _result(final_answer=None, reason="waiting")
    resumable_state["termination_reason"] = None
    _freeze_context(resumable_state, turn=resumable, conversation=conversation)
    checkpoints = MappingCheckpoints(
        {
            conflicted.id: conflicted_state,
            resumable.id: resumable_state,
        }
    )
    coordinator._checkpoints = checkpoints
    original_cas = conversation.compare_and_swap_context_manifest
    concurrent_manifest = (
        persisted_ahead[0],
        {**persisted_ahead[1], "source_id": "concurrent-direct"},
        persisted_ahead[2],
    )

    def race_context_manifest(
        turn_id: str,
        context_manifest: tuple[dict[str, JsonValue], ...],
        *,
        expected_context_manifest: tuple[dict[str, JsonValue], ...],
    ) -> Turn:
        if turn_id == conflicted.id:
            conversation.store_context_manifest(turn_id, concurrent_manifest)
        return original_cas(
            turn_id,
            context_manifest,
            expected_context_manifest=expected_context_manifest,
        )

    monkeypatch.setattr(
        conversation,
        "compare_and_swap_context_manifest",
        race_context_manifest,
    )

    results = await coordinator.reconcile_startup()

    by_turn = {result.turn_id: result for result in results}
    assert (by_turn[conflicted.id].status, by_turn[conflicted.id].error_code) == (
        RecoveryStatus.FAILED,
        "context_snapshot_conflict",
    )
    assert by_turn[resumable.id].status is RecoveryStatus.RESUMABLE
    conflict_result = conversation.read_thread(thread_id).turns[0]
    assert conflict_result.status is TurnStatus.FAILED
    assert conflict_result.context_manifest == concurrent_manifest
    assert resumable.id not in checkpoints.deleted


@pytest.mark.asyncio
async def test_resume_manifest_cas_conflict_returns_stable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = ResumeGraph(_result(final_answer="must not run", reason="completed"))
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        graph,
    )
    direct = conversation.append_direct_command(thread_id, "prior output", {})
    turn = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id="client_resume_manifest_conflict",
    )
    checkpoints.state = _result(final_answer=None, reason="waiting")
    checkpoints.state["termination_reason"] = None
    _freeze_context(checkpoints.state, turn=turn, conversation=conversation)
    checkpoint_manifest = tuple(checkpoints.state["context_manifest"])
    persisted_ahead = _manifest_with_optional_direct_command(
        checkpoint_manifest,
        entry_id=direct.id,
    )
    conversation.store_context_manifest(turn.id, persisted_ahead)
    original_cas = conversation.compare_and_swap_context_manifest
    concurrent_manifest = (
        persisted_ahead[0],
        {**persisted_ahead[1], "source_id": "concurrent-direct"},
        persisted_ahead[2],
    )

    def race_context_manifest(
        turn_id: str,
        context_manifest: tuple[dict[str, JsonValue], ...],
        *,
        expected_context_manifest: tuple[dict[str, JsonValue], ...],
    ) -> Turn:
        conversation.store_context_manifest(turn_id, concurrent_manifest)
        return original_cas(
            turn_id,
            context_manifest,
            expected_context_manifest=expected_context_manifest,
        )

    monkeypatch.setattr(
        conversation,
        "compare_and_swap_context_manifest",
        race_context_manifest,
    )

    with pytest.raises(TurnExecutionFailed, match="context_snapshot_conflict"):
        await coordinator.resume_unfinished(thread_id)

    recovered = conversation.read_thread(thread_id).turns[0]
    assert recovered.status is TurnStatus.FAILED
    assert recovered.error_code == "context_snapshot_conflict"
    assert recovered.context_manifest == concurrent_manifest
    assert checkpoints.deleted == [turn.id]
    assert graph.resume_inputs == []


@pytest.mark.asyncio
async def test_resume_rejects_and_cleans_checkpoint_without_frozen_context(
    tmp_path: Path,
) -> None:
    graph = ResumeGraph(_result(final_answer="must not run", reason="completed"))
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path, graph
    )
    turn = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id="client_missing_snapshot",
    )
    checkpoints.state = _result(final_answer=None, reason="waiting")
    checkpoints.state["turn_id"] = turn.id
    checkpoints.state["thread_id"] = turn.thread_id
    checkpoints.state["provider"] = turn.provider
    checkpoints.state["model"] = turn.model
    checkpoints.state["thinking_enabled"] = turn.thinking_enabled

    with pytest.raises(TurnExecutionFailed, match="context_snapshot_missing"):
        await coordinator.resume_unfinished(thread_id)

    recovered = conversation.read_thread(thread_id).turns[0]
    assert recovered.status is TurnStatus.FAILED
    assert recovered.error_code == "context_snapshot_missing"
    assert checkpoints.deleted == [turn.id]
    assert graph.resume_inputs == []


@pytest.mark.asyncio
async def test_resume_uses_injected_context_snapshot_validator(
    tmp_path: Path,
) -> None:
    observed: list[tuple[str, str, str]] = []

    def accept_snapshot(
        state: AgentState,
        *,
        turn: Turn,
        view: ThreadView,
    ) -> bool:
        observed.append((state["turn_id"], turn.id, view.thread.id))
        return True

    graph = ResumeGraph(_result(final_answer="resumed", reason="completed"))
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        graph,
        context_snapshot_validator=accept_snapshot,
    )
    turn = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id="client_custom_context_validator",
    )
    checkpoints.state = _result(final_answer=None, reason="waiting")
    _freeze_context(checkpoints.state, turn=turn, conversation=conversation)

    accepted = await coordinator.resume_unfinished(thread_id)
    await coordinator.wait(accepted.operation_id)

    assert observed == [(turn.id, turn.id, thread_id)]
    assert conversation.read_thread(thread_id).turns[0].status is TurnStatus.COMPLETED


@pytest.mark.asyncio
async def test_injected_context_validator_cannot_bypass_checkpoint_identity(
    tmp_path: Path,
) -> None:
    calls = 0

    def accept_snapshot(
        state: AgentState,
        *,
        turn: Turn,
        view: ThreadView,
    ) -> bool:
        nonlocal calls
        del state, turn, view
        calls += 1
        return True

    graph = ResumeGraph(_result(final_answer="must not run", reason="completed"))
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        graph,
        context_snapshot_validator=accept_snapshot,
    )
    turn = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id="client_identity_precedes_context_validator",
    )
    checkpoints.state = _result(final_answer=None, reason="waiting")

    with pytest.raises(TurnExecutionFailed, match="checkpoint_corrupt"):
        await coordinator.resume_unfinished(thread_id)

    assert calls == 0
    assert conversation.read_thread(thread_id).turns[0].error_code == (
        "checkpoint_corrupt"
    )
    assert checkpoints.deleted == [turn.id]
    assert graph.resume_inputs == []


@pytest.mark.asyncio
async def test_resume_terminalizes_corrupt_checkpoint_without_running_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = ResumeGraph(_result(final_answer="must not run", reason="completed"))
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path, graph
    )
    turn = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id="client_corrupt_checkpoint",
    )

    async def corrupt_state(turn_id: str) -> AgentState | None:
        raise CheckpointCorrupt(turn_id)

    monkeypatch.setattr(checkpoints, "latest_state", corrupt_state)

    with pytest.raises(TurnExecutionFailed, match="checkpoint_corrupt"):
        await coordinator.resume_unfinished(thread_id)

    recovered = conversation.read_thread(thread_id).turns[0]
    assert recovered.status is TurnStatus.FAILED
    assert recovered.error_code == "checkpoint_corrupt"
    assert checkpoints.deleted == [turn.id]
    assert graph.resume_inputs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_event",
    (EventType.TURN_STARTED, EventType.TURN_FAILED),
)
async def test_recovery_event_failure_cannot_mask_persisted_checkpoint_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_event: EventType,
) -> None:
    graph = ResumeGraph(_result(final_answer="must not run", reason="completed"))
    sink = FailOnTerminalSink(failed_event)
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        graph,
        event_sink=sink,
    )
    turn = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id="client_corrupt_event_failure",
    )

    async def corrupt_state(turn_id: str) -> AgentState | None:
        raise CheckpointCorrupt(turn_id)

    monkeypatch.setattr(checkpoints, "latest_state", corrupt_state)

    with pytest.raises(TurnExecutionFailed, match="checkpoint_corrupt"):
        await coordinator.resume_unfinished(thread_id)

    recovered = conversation.read_thread(thread_id).turns[0]
    assert recovered.status is TurnStatus.FAILED
    assert recovered.error_code == "checkpoint_corrupt"
    assert checkpoints.deleted == [turn.id]
    assert graph.resume_inputs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_event",
    (EventType.TURN_STARTED, EventType.TURN_COMPLETED),
)
async def test_recovery_event_failure_cannot_block_terminal_checkpoint_commit(
    tmp_path: Path,
    failed_event: EventType,
) -> None:
    graph = ResumeGraph(_result(final_answer="must not run", reason="completed"))
    sink = FailOnTerminalSink(failed_event)
    coordinator, conversation, _, checkpoints, _, thread_id = _coordinator(
        tmp_path,
        graph,
        event_sink=sink,
    )
    turn = conversation.begin_turn(
        thread_id,
        "inspect",
        _config(),
        client_message_id="client_terminal_event_failure",
    )
    checkpoints.state = _result(final_answer="recovered", reason="completed")
    _freeze_context(checkpoints.state, turn=turn, conversation=conversation)

    [result] = await coordinator.reconcile_startup()

    assert result.status is RecoveryStatus.FINALIZED
    recovered = conversation.read_thread(thread_id).turns[0]
    assert recovered.status is TurnStatus.COMPLETED
    assert conversation.read_thread(thread_id).entries[-1].content == "recovered"
    assert checkpoints.deleted == [turn.id]
    assert graph.resume_inputs == []
