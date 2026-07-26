from __future__ import annotations

import asyncio
import copy
import hashlib
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint

from awesome_agent.agent import AgentState, new_agent_state
from awesome_agent.application import composition
from awesome_agent.application.facade import LocalApplication
from awesome_agent.config import TurnConfig
from awesome_agent.context import calculate_context_budget, estimate_messages
from awesome_agent.conversation import Turn, TurnStatus
from awesome_agent.core.events import (
    CollectingEventSink,
    EventType,
    InteractionRequiredPayload,
    InteractionResolvedPayload,
)
from awesome_agent.core.workspace import WorkspaceTrustService, resolve_workspace
from awesome_agent.modeling import (
    AssistantMessage,
    SystemMessage,
    ToolCall,
    UserMessage,
)
from awesome_agent.protocol.jsonrpc import JsonRpcDispatcher
from awesome_agent.storage import ApplicationSQLite
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore
from awesome_agent.version import PRODUCT_VERSION


class CompletingRecoveryGraph:
    def __init__(self, states: dict[str, AgentState]) -> None:
        self._states = states
        self.calls: list[str] = []

    async def ainvoke(
        self,
        state: AgentState | None,
        config: dict[str, Any],
        *,
        context: object,
    ) -> AgentState:
        del context
        assert state is None
        turn_id = cast(str, config["configurable"]["thread_id"])
        self.calls.append(turn_id)
        recovered = copy.deepcopy(self._states[turn_id])
        recovered["pending_tool_calls"] = []
        recovered["next_tool_index"] = 0
        recovered["tool_results"] = []
        recovered["final_answer"] = f"recovered {turn_id}"
        recovered["termination_reason"] = "completed"
        return recovered


class BlockingRecoveryEventSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.recovery_emitted = asyncio.Event()
        self.release = asyncio.Event()

    async def emit(self, event: Any) -> None:
        await super().emit(event)
        if (
            isinstance(event.payload, InteractionRequiredPayload)
            and event.payload.interaction_kind == "recovery_decision"
        ):
            self.recovery_emitted.set()
            await self.release.wait()


class TurnCompletionBarrierSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.turn_completion_published = asyncio.Event()

    async def emit(self, event: Any) -> None:
        await super().emit(event)
        if event.event_type is EventType.TURN_COMPLETED:
            self.turn_completion_published.set()


class BlockingOperationStartedSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.operation_started = asyncio.Event()
        self.release = asyncio.Event()

    async def emit(self, event: Any) -> None:
        if event.event_type is EventType.OPERATION_STARTED:
            self.operation_started.set()
            await self.release.wait()
        await super().emit(event)


class FailOnceOnRecoveryResolvedSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def emit(self, event: Any) -> None:
        if (
            not self.failed
            and isinstance(event.payload, InteractionResolvedPayload)
            and event.payload.decision == "retry"
        ):
            self.failed = True
            raise RuntimeError("recovery resolution delivery failed")
        await super().emit(event)


class FailRecoveryResolvedSink(CollectingEventSink):
    def __init__(self, *, failures: int) -> None:
        super().__init__()
        self.failures_remaining = failures
        self.resolution_attempt_ids: list[str] = []

    async def emit(self, event: Any) -> None:
        if (
            isinstance(event.payload, InteractionResolvedPayload)
            and event.payload.decision == "retry"
        ):
            self.resolution_attempt_ids.append(event.payload.interaction_id)
            if self.failures_remaining > 0:
                self.failures_remaining -= 1
                raise RuntimeError("recovery resolution delivery failed")
        await super().emit(event)


class BlockingRecoveryResolvedSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.resolution_started = asyncio.Event()
        self.release = asyncio.Event()

    async def emit(self, event: Any) -> None:
        if (
            isinstance(event.payload, InteractionResolvedPayload)
            and event.payload.decision == "retry"
        ):
            self.resolution_started.set()
            await self.release.wait()
        await super().emit(event)


class FailOnceOnAbortResolvedSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def emit(self, event: Any) -> None:
        if (
            not self.failed
            and isinstance(event.payload, InteractionResolvedPayload)
            and event.payload.decision == "abort"
        ):
            self.failed = True
            raise RuntimeError("abort resolution delivery failed")
        await super().emit(event)


class BlockingOnceOnAbortResolvedSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.resolution_started = asyncio.Event()
        self.release = asyncio.Event()
        self.blocked = False

    async def emit(self, event: Any) -> None:
        if (
            not self.blocked
            and isinstance(event.payload, InteractionResolvedPayload)
            and event.payload.decision == "abort"
        ):
            self.blocked = True
            self.resolution_started.set()
            await self.release.wait()
        await super().emit(event)


class FailNextRecoveryRequiredSink(CollectingEventSink):
    def __init__(self, *, failures: int) -> None:
        super().__init__()
        self.failures_remaining = failures
        self.required_attempt_ids: list[str] = []

    async def emit(self, event: Any) -> None:
        if (
            isinstance(event.payload, InteractionRequiredPayload)
            and event.payload.interaction_kind == "recovery_decision"
        ):
            self.required_attempt_ids.append(event.payload.interaction_id)
            if len(self.required_attempt_ids) > 1 and self.failures_remaining > 0:
                self.failures_remaining -= 1
                raise RuntimeError("next recovery delivery failed")
        await super().emit(event)


class BlockingNextRecoveryRequiredSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.required_attempts = 0
        self.next_required_started = asyncio.Event()
        self.next_interaction_id: str | None = None

    async def emit(self, event: Any) -> None:
        if (
            isinstance(event.payload, InteractionRequiredPayload)
            and event.payload.interaction_kind == "recovery_decision"
        ):
            self.required_attempts += 1
            if self.required_attempts == 2:
                self.next_interaction_id = event.payload.interaction_id
                self.next_required_started.set()
                await asyncio.Event().wait()
        await super().emit(event)


def _success_value(frame: dict[str, Any] | None) -> dict[str, Any]:
    assert frame is not None
    result = frame["result"]
    assert result["ok"] is True
    return cast(dict[str, Any], result["value"])


async def _freeze_context(
    state: AgentState,
    turn: Turn,
    backend: composition._LocalApplicationBackend,
) -> None:
    content = "inspect"
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
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "covered_sequence_start": None,
            "covered_sequence_end": None,
        },
    ]
    state["context_estimated_tokens"] = estimate_messages((product, current))
    state["context_effective_limit"] = calculate_context_budget(
        turn.budgets.total_context_tokens,
        turn.budgets.total_context_tokens,
    ).effective_input_limit
    await backend._conversation.store_context_manifest(
        turn.id,
        tuple(state["context_manifest"]),
    )


async def _store_checkpoint(
    backend: composition._LocalApplicationBackend,
    *,
    name: str,
    tool_name: str | None = None,
) -> tuple[Turn, AgentState]:
    thread = await backend._conversation.create_thread(
        backend._workspace.key,
        name,
        current_model="deepseek/deepseek-v4-flash",
    )
    config: TurnConfig = backend._turn_config(thread)
    turn = await backend._conversation.begin_turn(
        thread.id,
        "inspect",
        config,
        client_message_id=f"client_{name}",
    )
    state = new_agent_state(
        thread_id=thread.id,
        turn_id=turn.id,
        workspace_key=backend._workspace.key,
        provider=turn.provider,
        model=turn.model,
        thinking_enabled=turn.thinking_enabled,
    )
    state["usage"] = {"input_tokens": 2, "output_tokens": 1}
    state["model_calls"] = 1
    await _freeze_context(state, turn, backend)
    if tool_name is not None:
        call = ToolCall(
            call_id=f"call_{name}",
            name=tool_name,
            arguments_json="{}",
        )
        assistant = AssistantMessage(tool_calls=(call,))
        state["pending_tool_calls"] = [call.model_dump(mode="json")]
        state["messages"].append(assistant.model_dump(mode="json"))
        state["context_estimated_tokens"] += estimate_messages((assistant,))
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = dict(state)
    saver = backend._saver
    assert saver is not None
    checkpoint_config: RunnableConfig = {
        "configurable": {"thread_id": turn.id, "checkpoint_ns": ""}
    }
    await saver.aput(checkpoint_config, checkpoint, {}, {})
    return turn, state


async def _seed_recovery_application(
    tmp_path: Path,
    *,
    tool_names: tuple[str | None, ...],
) -> tuple[Path, Path, list[Turn], dict[str, AgentState]]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = ApplicationSQLite(home / "state" / "application.db")
    await database.initialize()
    try:
        await WorkspaceTrustService(SQLiteWorkspaceTrustStore(database)).accept(
            resolve_workspace(workspace)
        )
    finally:
        await database.aclose()
    first = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={"DEEPSEEK_API_KEY": "test-key"},
        gateway_factory=lambda provider, model: cast(Any, object()),
    )
    assert (await first.initialize()).ok is True
    backend = cast(composition._LocalApplicationBackend, first._backend)
    turns: list[Turn] = []
    states: dict[str, AgentState] = {}
    for index, tool_name in enumerate(tool_names, start=1):
        turn, state = await _store_checkpoint(
            backend,
            name=f"recovery_{index}",
            tool_name=tool_name,
        )
        turns.append(turn)
        states[turn.id] = state
    assert (await first.shutdown()).ok is True
    return home, workspace, turns, states


async def _restart_with_graph(
    home: Path,
    workspace: Path,
    graph: CompletingRecoveryGraph,
    sink: CollectingEventSink,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[LocalApplication, composition._LocalApplicationBackend, JsonRpcDispatcher]:
    monkeypatch.setattr(composition, "compile_agent_graph", lambda saver: graph)
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=sink,
        environ={"DEEPSEEK_API_KEY": "test-key"},
        gateway_factory=lambda provider, model: cast(Any, object()),
    )
    backend = cast(composition._LocalApplicationBackend, application._backend)
    dispatcher = JsonRpcDispatcher(application)
    initialized = await dispatcher.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocol_version": 3,
                "client_name": "awesome",
                "client_version": PRODUCT_VERSION,
            },
        }
    )
    assert _success_value(initialized)["status"] == "ready"
    return application, backend, dispatcher


def _recovery_events(sink: CollectingEventSink) -> list[Any]:
    return [
        event
        for event in sink.events
        if isinstance(event.payload, InteractionRequiredPayload)
        and event.payload.interaction_kind == "recovery_decision"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", [None, "execute", "mcp.example.change"])
async def test_checkpoint_retries_at_most_once_through_application_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str | None,
) -> None:
    home, workspace, [turn], states = await _seed_recovery_application(
        tmp_path,
        tool_names=(tool_name,),
    )
    sink = CollectingEventSink()
    graph = CompletingRecoveryGraph(states)
    application, backend, dispatcher = await _restart_with_graph(
        home,
        workspace,
        graph,
        sink,
        monkeypatch,
    )
    [required] = _recovery_events(sink)
    assert required.turn_id == turn.id
    assert tuple(choice.decision for choice in required.payload.choices) == (
        ("retry", "abort") if tool_name is None else ("abort", "retry")
    )
    assert required.payload.target == (
        f"unfinished Turn {turn.id}"
        if tool_name is None
        else "uncertain external tool call"
    )
    state = _success_value(
        await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "application.getState",
                "params": {},
            }
        )
    )
    assert state["pending_interaction_id"] == required.payload.interaction_id

    resolved = _success_value(
        await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "interaction.respond",
                "params": {
                    "interaction_id": required.payload.interaction_id,
                    "decision": "retry",
                },
            }
        )
    )
    assert resolved == {"accepted": True, "status": "resolved"}
    operation_id = next(
        event.operation_id
        for event in sink.events
        if event.event_type is EventType.OPERATION_STARTED and event.turn_id == turn.id
    )
    assert operation_id is not None
    assert backend._runtime is not None
    await backend._runtime.turns.wait(operation_id)

    stale = _success_value(
        await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "interaction.respond",
                "params": {
                    "interaction_id": required.payload.interaction_id,
                    "decision": "retry",
                },
            }
        )
    )
    assert stale["accepted"] is False
    assert stale["status"] == "not_found"
    assert graph.calls == [turn.id]
    assert (await backend._conversation.read_thread(turn.thread_id)).turns[
        0
    ].status is TurnStatus.COMPLETED
    await application.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["execute", "mcp.example.change"])
async def test_uncertain_external_checkpoint_aborts_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    home, workspace, [turn], states = await _seed_recovery_application(
        tmp_path,
        tool_names=(tool_name,),
    )
    sink = CollectingEventSink()
    graph = CompletingRecoveryGraph(states)
    application, backend, dispatcher = await _restart_with_graph(
        home,
        workspace,
        graph,
        sink,
        monkeypatch,
    )
    [required] = _recovery_events(sink)
    assert required.turn_id == turn.id
    assert required.payload.target == "uncertain external tool call"

    aborted = _success_value(
        await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "interaction.respond",
                "params": {
                    "interaction_id": required.payload.interaction_id,
                    "decision": "abort",
                },
            }
        )
    )

    assert aborted["accepted"] is True
    recovered = (await backend._conversation.read_thread(turn.thread_id)).turns[0]
    assert recovered.status is TurnStatus.FAILED
    assert recovered.error_code == "recovery_aborted"
    assert backend._checkpoints is not None
    assert await backend._checkpoints.exists(turn.id) is False
    assert graph.calls == []
    assert not any(
        event.event_type is EventType.OPERATION_STARTED for event in sink.events
    )
    await application.shutdown()


@pytest.mark.asyncio
async def test_recovery_queue_presents_one_item_and_rejects_old_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, turns, states = await _seed_recovery_application(
        tmp_path,
        tool_names=(None, "execute"),
    )
    sink = CollectingEventSink()
    graph = CompletingRecoveryGraph(states)
    application, backend, dispatcher = await _restart_with_graph(
        home,
        workspace,
        graph,
        sink,
        monkeypatch,
    )
    [first] = _recovery_events(sink)
    first_id = first.payload.interaction_id

    first_response = _success_value(
        await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "interaction.respond",
                "params": {"interaction_id": first_id, "decision": "retry"},
            }
        )
    )
    assert first_response["accepted"] is True
    first_operation_id = next(
        event.operation_id
        for event in sink.events
        if event.event_type is EventType.OPERATION_STARTED
        and event.turn_id == first.turn_id
    )
    assert first_operation_id is not None
    assert backend._runtime is not None
    await backend._runtime.turns.wait(first_operation_id)
    [_, second] = _recovery_events(sink)
    assert second.payload.interaction_id != first_id
    assert second.turn_id != first.turn_id
    interaction_events = [
        event
        for event in sink.events
        if event.event_type
        in {EventType.INTERACTION_REQUIRED, EventType.INTERACTION_RESOLVED}
    ]
    assert [event.event_type for event in interaction_events] == [
        EventType.INTERACTION_REQUIRED,
        EventType.INTERACTION_RESOLVED,
        EventType.INTERACTION_REQUIRED,
    ]

    stale = _success_value(
        await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "interaction.respond",
                "params": {"interaction_id": first_id, "decision": "abort"},
            }
        )
    )
    assert stale == {"accepted": False, "status": "not_found"}
    current_state = _success_value(
        await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "application.getState",
                "params": {},
            }
        )
    )
    assert current_state["pending_interaction_id"] == second.payload.interaction_id

    _success_value(
        await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "interaction.respond",
                "params": {
                    "interaction_id": second.payload.interaction_id,
                    "decision": "abort",
                },
            }
        )
    )
    assert graph.calls == [first.turn_id]
    statuses = {
        turn.id: (await backend._conversation.read_thread(turn.thread_id)).turns[0]
        for turn in turns
    }
    assert statuses[first.turn_id].status is TurnStatus.COMPLETED
    assert statuses[second.turn_id].error_code == "recovery_aborted"
    await application.shutdown()


@pytest.mark.asyncio
async def test_retry_publishes_resolution_before_presenting_next_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, turns, states = await _seed_recovery_application(
        tmp_path,
        tool_names=(None, "execute"),
    )
    sink = TurnCompletionBarrierSink()
    graph = CompletingRecoveryGraph(states)
    application, backend, dispatcher = await _restart_with_graph(
        home,
        workspace,
        graph,
        sink,
        monkeypatch,
    )
    assert backend._runtime is not None
    turn_coordinator = backend._runtime.turns
    resume_unfinished = turn_coordinator.resume_unfinished

    async def delay_response_until_turn_completion(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        accepted = await resume_unfinished(*args, **kwargs)
        await sink.turn_completion_published.wait()
        return accepted

    monkeypatch.setattr(
        turn_coordinator,
        "resume_unfinished",
        delay_response_until_turn_completion,
    )
    [first] = _recovery_events(sink)

    resolved = _success_value(
        await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "interaction.respond",
                "params": {
                    "interaction_id": first.payload.interaction_id,
                    "decision": "retry",
                },
            }
        )
    )
    assert resolved == {"accepted": True, "status": "resolved"}
    operation_id = next(
        event.operation_id
        for event in sink.events
        if event.event_type is EventType.OPERATION_STARTED
        and event.turn_id == first.turn_id
    )
    assert operation_id is not None
    await turn_coordinator.wait(operation_id)

    interaction_events = [
        event
        for event in sink.events
        if event.event_type
        in {EventType.INTERACTION_REQUIRED, EventType.INTERACTION_RESOLVED}
    ]
    assert [event.event_type for event in interaction_events] == [
        EventType.INTERACTION_REQUIRED,
        EventType.INTERACTION_RESOLVED,
        EventType.INTERACTION_REQUIRED,
    ]
    assert interaction_events[0].turn_id == first.turn_id
    assert interaction_events[1].turn_id == first.turn_id
    assert interaction_events[2].turn_id == next(
        turn.id for turn in turns if turn.id != first.turn_id
    )
    second_payload = interaction_events[2].payload
    assert isinstance(second_payload, InteractionRequiredPayload)

    _success_value(
        await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "interaction.respond",
                "params": {
                    "interaction_id": second_payload.interaction_id,
                    "decision": "abort",
                },
            }
        )
    )
    await application.shutdown()


@pytest.mark.asyncio
async def test_retry_claim_survives_response_cancellation_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, [turn], states = await _seed_recovery_application(
        tmp_path,
        tool_names=(None,),
    )
    sink = BlockingOperationStartedSink()
    graph = CompletingRecoveryGraph(states)
    application, backend, _ = await _restart_with_graph(
        home,
        workspace,
        graph,
        sink,
        monkeypatch,
    )
    [required] = _recovery_events(sink)
    responding = asyncio.create_task(
        backend.resolve_interaction(required.payload.interaction_id, "retry")
    )
    await asyncio.wait_for(sink.operation_started.wait(), timeout=1)

    responding.cancel()
    await asyncio.sleep(0)
    responding.cancel()
    sink.release.set()
    with pytest.raises(asyncio.CancelledError):
        await responding

    operation_events = [
        event
        for event in sink.events
        if event.event_type is EventType.OPERATION_STARTED
    ]
    assert len(operation_events) == 1
    operation_id = operation_events[0].operation_id
    assert operation_id is not None
    assert backend._runtime is not None
    await backend._runtime.turns.wait(operation_id)
    stale = await backend.resolve_interaction(
        required.payload.interaction_id,
        "retry",
    )

    assert stale.accepted is False
    assert stale.status == "not_found"
    assert graph.calls == [turn.id]
    assert (await backend._conversation.read_thread(turn.thread_id)).turns[
        0
    ].status is TurnStatus.COMPLETED
    assert backend._interactions.pending is None
    assert backend._recovery_queue == []
    await application.shutdown()


@pytest.mark.asyncio
async def test_retry_emitter_failure_does_not_replay_claimed_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, turns, states = await _seed_recovery_application(
        tmp_path,
        tool_names=(None, "execute"),
    )
    sink = FailOnceOnRecoveryResolvedSink()
    graph = CompletingRecoveryGraph(states)
    application, backend, _ = await _restart_with_graph(
        home,
        workspace,
        graph,
        sink,
        monkeypatch,
    )
    [first] = _recovery_events(sink)

    resolved = await backend.resolve_interaction(first.payload.interaction_id, "retry")
    assert resolved.accepted is True
    assert sink.failed is True
    operation_id = next(
        event.operation_id
        for event in sink.events
        if event.event_type is EventType.OPERATION_STARTED
        and event.turn_id == first.turn_id
    )
    assert operation_id is not None
    assert backend._runtime is not None
    await backend._runtime.turns.wait(operation_id)

    stale = await backend.resolve_interaction(first.payload.interaction_id, "retry")
    assert stale.accepted is False
    assert stale.status == "not_found"
    assert graph.calls == [first.turn_id]
    [_, second] = _recovery_events(sink)
    interaction_events = [
        event
        for event in sink.events
        if event.event_type
        in {EventType.INTERACTION_REQUIRED, EventType.INTERACTION_RESOLVED}
    ]
    assert [event.event_type for event in interaction_events] == [
        EventType.INTERACTION_REQUIRED,
        EventType.INTERACTION_RESOLVED,
        EventType.INTERACTION_REQUIRED,
    ]
    assert second.turn_id == next(turn.id for turn in turns if turn.id != first.turn_id)
    aborted = await backend.resolve_interaction(
        second.payload.interaction_id,
        "abort",
    )
    assert aborted.accepted is True
    assert graph.calls == [first.turn_id]
    await application.shutdown()


@pytest.mark.asyncio
async def test_continuous_retry_resolution_failure_blocks_next_until_reinitialize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, turns, states = await _seed_recovery_application(
        tmp_path,
        tool_names=(None, None),
    )
    sink = FailRecoveryResolvedSink(failures=2)
    graph = CompletingRecoveryGraph(states)
    application, backend, _ = await _restart_with_graph(
        home,
        workspace,
        graph,
        sink,
        monkeypatch,
    )
    [first] = _recovery_events(sink)

    with pytest.raises(RuntimeError, match="resolution delivery failed"):
        await backend.resolve_interaction(first.payload.interaction_id, "retry")
    operation_id = next(
        event.operation_id
        for event in sink.events
        if event.event_type is EventType.OPERATION_STARTED
        and event.turn_id == first.turn_id
    )
    assert operation_id is not None
    assert backend._runtime is not None
    await backend._runtime.turns.wait(operation_id)

    assert sink.resolution_attempt_ids == [
        first.payload.interaction_id,
        first.payload.interaction_id,
    ]
    assert backend._recovery_resolution_delivery is not None
    assert backend._interactions.pending is None
    assert _recovery_events(sink) == [first]
    assert graph.calls == [first.turn_id]

    sink.failures_remaining = 0
    initialized = await backend.initialize_application()

    assert initialized.status.value == "ready"
    [_, second] = _recovery_events(sink)
    assert sink.resolution_attempt_ids[-1] == first.payload.interaction_id
    interaction_events = [
        event
        for event in sink.events
        if event.event_type
        in {EventType.INTERACTION_REQUIRED, EventType.INTERACTION_RESOLVED}
    ]
    assert [event.event_type for event in interaction_events] == [
        EventType.INTERACTION_REQUIRED,
        EventType.INTERACTION_RESOLVED,
        EventType.INTERACTION_REQUIRED,
    ]
    assert second.turn_id == next(turn.id for turn in turns if turn.id != first.turn_id)
    assert (
        await backend.resolve_interaction(first.payload.interaction_id, "retry")
    ).status == "not_found"
    await backend.resolve_interaction(second.payload.interaction_id, "abort")
    await application.shutdown()


@pytest.mark.asyncio
async def test_retry_resolution_cancellation_releases_next_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, turns, states = await _seed_recovery_application(
        tmp_path,
        tool_names=(None, "execute"),
    )
    sink = BlockingRecoveryResolvedSink()
    graph = CompletingRecoveryGraph(states)
    application, backend, _ = await _restart_with_graph(
        home,
        workspace,
        graph,
        sink,
        monkeypatch,
    )
    [first] = _recovery_events(sink)
    responding = asyncio.create_task(
        backend.resolve_interaction(first.payload.interaction_id, "retry")
    )
    await asyncio.wait_for(sink.resolution_started.wait(), timeout=1)

    responding.cancel()
    await asyncio.sleep(0)
    responding.cancel()
    sink.release.set()
    with pytest.raises(asyncio.CancelledError):
        await responding
    operation_id = next(
        event.operation_id
        for event in sink.events
        if event.event_type is EventType.OPERATION_STARTED
        and event.turn_id == first.turn_id
    )
    assert operation_id is not None
    assert backend._runtime is not None
    await backend._runtime.turns.wait(operation_id)

    stale = await backend.resolve_interaction(first.payload.interaction_id, "retry")
    assert stale.accepted is False
    assert stale.status == "not_found"
    assert graph.calls == [first.turn_id]
    [_, second] = _recovery_events(sink)
    interaction_events = [
        event
        for event in sink.events
        if event.event_type
        in {EventType.INTERACTION_REQUIRED, EventType.INTERACTION_RESOLVED}
    ]
    assert [event.event_type for event in interaction_events] == [
        EventType.INTERACTION_REQUIRED,
        EventType.INTERACTION_RESOLVED,
        EventType.INTERACTION_REQUIRED,
    ]
    assert second.turn_id == next(turn.id for turn in turns if turn.id != first.turn_id)
    aborted = await backend.resolve_interaction(
        second.payload.interaction_id,
        "abort",
    )
    assert aborted.accepted is True
    assert graph.calls == [first.turn_id]
    await application.shutdown()


@pytest.mark.asyncio
async def test_abort_resolution_failure_once_advances_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, turns, states = await _seed_recovery_application(
        tmp_path,
        tool_names=(None, None),
    )
    sink = FailOnceOnAbortResolvedSink()
    graph = CompletingRecoveryGraph(states)
    application, backend, _ = await _restart_with_graph(
        home,
        workspace,
        graph,
        sink,
        monkeypatch,
    )
    [first] = _recovery_events(sink)

    resolved = await backend.resolve_interaction(
        first.payload.interaction_id,
        "abort",
    )

    assert resolved.accepted is True
    assert sink.failed is True
    [_, second] = _recovery_events(sink)
    interaction_events = [
        event
        for event in sink.events
        if event.event_type
        in {EventType.INTERACTION_REQUIRED, EventType.INTERACTION_RESOLVED}
    ]
    assert [event.event_type for event in interaction_events] == [
        EventType.INTERACTION_REQUIRED,
        EventType.INTERACTION_RESOLVED,
        EventType.INTERACTION_REQUIRED,
    ]
    resolution = interaction_events[1].payload
    assert isinstance(resolution, InteractionResolvedPayload)
    assert resolution.interaction_id == first.payload.interaction_id
    assert second.payload.interaction_id != first.payload.interaction_id
    assert (
        await backend.resolve_interaction(first.payload.interaction_id, "abort")
    ).status == "not_found"
    assert graph.calls == []
    failed = [
        (await backend._conversation.read_thread(turn.thread_id)).turns[0].status
        is TurnStatus.FAILED
        for turn in turns
    ]
    assert sum(failed) == 1

    await backend.resolve_interaction(second.payload.interaction_id, "abort")
    await application.shutdown()


@pytest.mark.asyncio
async def test_abort_resolution_repeated_cancellation_finishes_once_before_next(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, turns, states = await _seed_recovery_application(
        tmp_path,
        tool_names=(None, None),
    )
    sink = BlockingOnceOnAbortResolvedSink()
    graph = CompletingRecoveryGraph(states)
    application, backend, _ = await _restart_with_graph(
        home,
        workspace,
        graph,
        sink,
        monkeypatch,
    )
    [first] = _recovery_events(sink)
    responding = asyncio.create_task(
        backend.resolve_interaction(first.payload.interaction_id, "abort")
    )
    await asyncio.wait_for(sink.resolution_started.wait(), timeout=1)

    responding.cancel()
    await asyncio.sleep(0)
    responding.cancel()
    sink.release.set()
    with pytest.raises(asyncio.CancelledError):
        await responding

    [_, second] = _recovery_events(sink)
    interaction_events = [
        event
        for event in sink.events
        if event.event_type
        in {EventType.INTERACTION_REQUIRED, EventType.INTERACTION_RESOLVED}
    ]
    assert [event.event_type for event in interaction_events] == [
        EventType.INTERACTION_REQUIRED,
        EventType.INTERACTION_RESOLVED,
        EventType.INTERACTION_REQUIRED,
    ]
    resolution = interaction_events[1].payload
    assert isinstance(resolution, InteractionResolvedPayload)
    assert resolution.interaction_id == first.payload.interaction_id
    assert (
        await backend.resolve_interaction(first.payload.interaction_id, "abort")
    ).status == "not_found"
    assert graph.calls == []
    failed = [
        (await backend._conversation.read_thread(turn.thread_id)).turns[0].status
        is TurnStatus.FAILED
        for turn in turns
    ]
    assert sum(failed) == 1

    await backend.resolve_interaction(second.payload.interaction_id, "abort")
    await application.shutdown()


@pytest.mark.asyncio
async def test_next_recovery_required_failure_once_reuses_interaction_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, _, states = await _seed_recovery_application(
        tmp_path,
        tool_names=(None, None),
    )
    sink = FailNextRecoveryRequiredSink(failures=1)
    graph = CompletingRecoveryGraph(states)
    application, backend, _ = await _restart_with_graph(
        home,
        workspace,
        graph,
        sink,
        monkeypatch,
    )
    [first] = _recovery_events(sink)

    resolved = await backend.resolve_interaction(first.payload.interaction_id, "retry")
    assert resolved.accepted is True
    operation_id = next(
        event.operation_id
        for event in sink.events
        if event.event_type is EventType.OPERATION_STARTED
        and event.turn_id == first.turn_id
    )
    assert operation_id is not None
    assert backend._runtime is not None
    await backend._runtime.turns.wait(operation_id)

    [_, second] = _recovery_events(sink)
    assert sink.required_attempt_ids == [
        first.payload.interaction_id,
        second.payload.interaction_id,
        second.payload.interaction_id,
    ]
    assert backend._interactions.pending is not None
    assert backend._interactions.pending.id == second.payload.interaction_id
    assert graph.calls == [first.turn_id]

    await backend.resolve_interaction(second.payload.interaction_id, "abort")
    await application.shutdown()


@pytest.mark.asyncio
async def test_continuous_next_required_failure_keeps_same_pending_for_reinitialize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, turns, states = await _seed_recovery_application(
        tmp_path,
        tool_names=(None, None),
    )
    sink = FailNextRecoveryRequiredSink(failures=2)
    graph = CompletingRecoveryGraph(states)
    application, backend, _ = await _restart_with_graph(
        home,
        workspace,
        graph,
        sink,
        monkeypatch,
    )
    [first] = _recovery_events(sink)

    resolved = await backend.resolve_interaction(first.payload.interaction_id, "retry")
    assert resolved.accepted is True
    operation_id = next(
        event.operation_id
        for event in sink.events
        if event.event_type is EventType.OPERATION_STARTED
        and event.turn_id == first.turn_id
    )
    assert operation_id is not None
    assert backend._runtime is not None
    await backend._runtime.turns.wait(operation_id)

    pending = backend._interactions.pending
    assert pending is not None
    assert pending.turn_id == next(
        turn.id for turn in turns if turn.id != first.turn_id
    )
    assert sink.required_attempt_ids[-2:] == [pending.id, pending.id]
    assert _recovery_events(sink) == [first]

    sink.failures_remaining = 0
    initialized = await backend.initialize_application()

    assert initialized.status.value == "ready"
    [_, second] = _recovery_events(sink)
    assert second.payload.interaction_id == pending.id
    assert sink.required_attempt_ids[-1] == pending.id
    await backend.resolve_interaction(second.payload.interaction_id, "abort")
    await application.shutdown()


@pytest.mark.asyncio
async def test_shutdown_during_next_recovery_delivery_preserves_pending_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, _, states = await _seed_recovery_application(
        tmp_path,
        tool_names=(None, None),
    )
    sink = BlockingNextRecoveryRequiredSink()
    graph = CompletingRecoveryGraph(states)
    application, backend, _ = await _restart_with_graph(
        home,
        workspace,
        graph,
        sink,
        monkeypatch,
    )
    [first] = _recovery_events(sink)

    resolved = await backend.resolve_interaction(first.payload.interaction_id, "retry")
    assert resolved.accepted is True
    await asyncio.wait_for(sink.next_required_started.wait(), timeout=1)

    shutdown = await asyncio.wait_for(application.shutdown(), timeout=2)

    assert shutdown.ok is True
    assert sink.next_interaction_id is not None
    assert backend._interactions.pending is not None
    assert backend._interactions.pending.id == sink.next_interaction_id
    assert backend._recovery_queue


@pytest.mark.asyncio
async def test_recovery_response_waits_for_startup_bootstrap_to_finish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, [turn], states = await _seed_recovery_application(
        tmp_path,
        tool_names=(None,),
    )
    graph = CompletingRecoveryGraph(states)
    monkeypatch.setattr(composition, "compile_agent_graph", lambda saver: graph)
    sink = BlockingRecoveryEventSink()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=sink,
        environ={"DEEPSEEK_API_KEY": "test-key"},
        gateway_factory=lambda provider, model: cast(Any, object()),
    )
    backend = cast(composition._LocalApplicationBackend, application._backend)
    dispatcher = JsonRpcDispatcher(application)
    initializing = asyncio.create_task(
        dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocol_version": 3,
                    "client_name": "awesome",
                    "client_version": PRODUCT_VERSION,
                },
            }
        )
    )
    await asyncio.wait_for(sink.recovery_emitted.wait(), timeout=1)
    [required] = _recovery_events(sink)
    responding = asyncio.create_task(
        dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "interaction.respond",
                "params": {
                    "interaction_id": required.payload.interaction_id,
                    "decision": "abort",
                },
            }
        )
    )
    await asyncio.sleep(0)
    assert responding.done() is False

    sink.release.set()
    assert _success_value(await initializing)["status"] == "ready"
    assert _success_value(await responding)["accepted"] is True
    recovered = (await backend._conversation.read_thread(turn.thread_id)).turns[0]
    assert recovered.error_code == "recovery_aborted"
    assert graph.calls == []
    await application.shutdown()
