import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest

from awesome_agent.agent import AgentRuntimeContext, AgentState, new_agent_state
from awesome_agent.application.events import ApplicationEventProjector
from awesome_agent.application.operations import OperationController
from awesome_agent.application.turns import TurnCoordinator, TurnExecutionFailed
from awesome_agent.config import BudgetConfig, TurnConfig
from awesome_agent.conversation import ConversationService, TurnStatus
from awesome_agent.core.events import (
    CollectingEventSink,
    EventEmitter,
    EventEnvelope,
    EventType,
)
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


class BlockingOperationStartedSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()

    async def emit(self, event: EventEnvelope) -> None:
        if event.event_type is EventType.OPERATION_STARTED:
            self.entered.set()
            await asyncio.Event().wait()
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


def _coordinator(
    tmp_path: Path,
    graph: FakeGraph,
    *,
    turn_extension_preparer: Callable[[], Awaitable[None]] | None = None,
    event_sink: CollectingEventSink | None = None,
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
        turn_extension_preparer=(turn_extension_preparer or default_extension_preparer),
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
    assert coordinator.active_operation_id is None


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

    accepted = await coordinator.resume_unfinished(thread_id)
    await coordinator.wait(accepted.operation_id)

    assert accepted.turn_id == turn.id
    assert graph.resume_inputs == [None]
    view = conversation.read_thread(thread_id)
    assert len(view.entries) == 2
    assert view.entries[-1].content == "resumed"
