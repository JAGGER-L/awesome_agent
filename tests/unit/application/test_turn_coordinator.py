import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from awesome_agent.agent import AgentRuntimeContext, AgentState, new_agent_state
from awesome_agent.application.events import ApplicationEventProjector
from awesome_agent.application.operations import OperationController
from awesome_agent.application.turns import TurnCoordinator, TurnExecutionFailed
from awesome_agent.config import BudgetConfig, TurnConfig
from awesome_agent.conversation import ConversationService, TurnStatus
from awesome_agent.core.events import CollectingEventSink, EventEmitter, EventType
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


class FakeCheckpoints:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def exists(self, turn_id: str) -> bool:
        return turn_id not in self.deleted

    async def latest_state(self, turn_id: str) -> AgentState | None:
        del turn_id
        return None

    async def delete(self, turn_id: str) -> None:
        self.deleted.append(turn_id)


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
    sink = CollectingEventSink()
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
    )
    return coordinator, conversation, sink, checkpoints, repositories, thread.id


@pytest.mark.asyncio
async def test_submit_turn_freezes_config_commits_then_emits_and_cleans_checkpoint(
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    graph = FakeGraph(_result(final_answer="done", reason="completed"), gate)
    coordinator, conversation, sink, checkpoints, repositories, thread_id = (
        _coordinator(tmp_path, graph)
    )

    accepted = await coordinator.submit_turn(thread_id, "inspect")
    assert accepted.thread_id == thread_id
    assert accepted.turn_id is not None
    assert accepted.operation_id
    assert coordinator.active_operation_id == accepted.operation_id

    view = conversation.read_thread(thread_id)
    repositories.threads.update(
        view.thread.model_copy(
            update={
                "current_model": "kimi/kimi-k2.6",
                "thinking_enabled": True,
            }
        )
    )
    gate.set()
    await coordinator.wait(accepted.operation_id)

    completed = conversation.read_thread(thread_id)
    assert completed.turns[0].status is TurnStatus.COMPLETED
    assert completed.entries[-1].content == "done"
    assert graph.inputs[0]["provider"] == "deepseek"
    assert graph.inputs[0]["thinking_enabled"] is False
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

    accepted = await coordinator.submit_turn(thread_id, "inspect")
    with pytest.raises(TurnExecutionFailed, match="model_authentication"):
        await coordinator.wait(accepted.operation_id)

    turn = conversation.read_thread(thread_id).turns[0]
    assert turn.status is TurnStatus.FAILED
    assert turn.error_code == "model_authentication"
    assert checkpoints.deleted == [accepted.turn_id]
    assert [event.event_type for event in sink.events][-2:] == [
        EventType.TURN_FAILED,
        EventType.OPERATION_FAILED,
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
    accepted = await coordinator.submit_turn(thread_id, "inspect")

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
