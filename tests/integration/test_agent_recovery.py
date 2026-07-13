from pathlib import Path
from typing import Any, cast

import pytest

from awesome_agent.agent import AgentRuntimeContext, AgentState, new_agent_state
from awesome_agent.application.operations import OperationController
from awesome_agent.application.turns import RecoveryStatus, TurnCoordinator
from awesome_agent.config import BudgetConfig, TurnConfig
from awesome_agent.conversation import (
    ConversationService,
    Turn,
    TurnStatus,
    UsageSummary,
)
from awesome_agent.core.events import CollectingEventSink, EventEmitter, EventType
from awesome_agent.storage.checkpoints import CheckpointCorrupt
from awesome_agent.storage.conversations import SQLiteConversationRepositories


class UnusedGraph:
    async def ainvoke(self, *args: object, **kwargs: object) -> AgentState:
        raise AssertionError("reconciliation must not execute the graph")


class RecoveryCheckpoints:
    def __init__(self) -> None:
        self.states: dict[str, AgentState | Exception] = {}
        self.deleted: list[str] = []
        self.observations: list[str] = []

    async def exists(self, turn_id: str) -> bool:
        self.observations.append(f"exists:{turn_id}")
        return turn_id in self.states and turn_id not in self.deleted

    async def latest_state(self, turn_id: str) -> AgentState | None:
        self.observations.append(f"latest:{turn_id}")
        value = self.states.get(turn_id)
        if isinstance(value, Exception):
            raise value
        return value

    async def delete(self, turn_id: str) -> None:
        self.deleted.append(turn_id)


def _state(
    turn: Turn, *, answer: str | None = None, reason: str | None = None
) -> AgentState:
    state = new_agent_state(
        thread_id=turn.thread_id,
        turn_id=turn.id,
        workspace_key="workspace_1",
        provider=turn.provider,
        model=turn.model,
        thinking_enabled=turn.thinking_enabled,
    )
    state["final_answer"] = answer
    state["termination_reason"] = reason
    state["usage"] = {"input_tokens": 2, "output_tokens": 1}
    state["model_calls"] = 1
    state["context_manifest"] = [{"kind": "history", "estimated_tokens": 3}]
    return state


def _turn(conversation: ConversationService, thread_id: str) -> Turn:
    return conversation.begin_turn(
        thread_id,
        "inspect",
        TurnConfig(
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            budgets=BudgetConfig(),
        ),
        client_message_id="client_recovery",
    )


@pytest.mark.asyncio
async def test_startup_reconciles_complete_resumable_missing_corrupt_and_leftover(
    tmp_path: Path,
) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    turns: dict[str, Turn] = {}
    for name in ("final", "resume", "missing", "corrupt", "leftover"):
        thread = conversation.create_thread("workspace_1", name)
        turns[name] = _turn(conversation, thread.id)
    conversation.complete_turn(
        turns["leftover"].id,
        "already committed",
        UsageSummary(),
        "completed",
    )

    checkpoints = RecoveryCheckpoints()
    checkpoints.states = {
        turns["final"].id: _state(
            turns["final"], answer="recovered answer", reason="completed"
        ),
        turns["resume"].id: _state(turns["resume"]),
        turns["corrupt"].id: CheckpointCorrupt(turns["corrupt"].id),
        turns["leftover"].id: _state(
            turns["leftover"], answer="already committed", reason="completed"
        ),
    }
    order: list[str] = []
    original_exists = checkpoints.exists

    async def observed_exists(turn_id: str) -> bool:
        order.append("checkpoint")
        return await original_exists(turn_id)

    checkpoints.exists = observed_exists  # type: ignore[method-assign]
    sink = CollectingEventSink()
    emitter = EventEmitter(
        session_id="session_1",
        workspace_key="workspace_1",
        sink=sink,
    )
    coordinator = TurnCoordinator(
        workspace_key="workspace_1",
        conversation=conversation,
        config_resolver=lambda thread: cast(Any, None),
        graph=cast(Any, UnusedGraph()),
        runtime_context_factory=lambda turn, operation, projector: cast(
            AgentRuntimeContext, object()
        ),
        operations=OperationController(emitter),
        emitter=emitter,
        checkpoints=checkpoints,
        seal_changes=lambda turn_id: None,
        reconcile_changes=lambda: order.append("changes"),
    )

    results = await coordinator.reconcile_startup()

    assert order[0] == "changes"
    statuses = {result.turn_id: result.status for result in results}
    assert statuses == {
        turns["final"].id: RecoveryStatus.FINALIZED,
        turns["resume"].id: RecoveryStatus.RESUMABLE,
        turns["missing"].id: RecoveryStatus.FAILED,
        turns["corrupt"].id: RecoveryStatus.FAILED,
        turns["leftover"].id: RecoveryStatus.CLEANED,
    }
    assert (
        conversation.read_thread(turns["final"].thread_id).turns[0].status
        is TurnStatus.COMPLETED
    )
    missing = conversation.read_thread(turns["missing"].thread_id).turns[0]
    corrupt = conversation.read_thread(turns["corrupt"].thread_id).turns[0]
    assert (missing.status, missing.error_code) == (
        TurnStatus.FAILED,
        "checkpoint_missing",
    )
    assert (corrupt.status, corrupt.error_code) == (
        TurnStatus.FAILED,
        "checkpoint_corrupt",
    )
    finalized = conversation.read_thread(turns["final"].thread_id).turns[0]
    assert finalized.usage == UsageSummary(
        input_tokens=2,
        output_tokens=1,
        model_calls=1,
    )
    assert finalized.context_manifest == ({"kind": "history", "estimated_tokens": 3},)
    assert turns["resume"].id not in checkpoints.deleted
    assert {
        turns["final"].id,
        turns["missing"].id,
        turns["corrupt"].id,
        turns["leftover"].id,
    } <= set(checkpoints.deleted)


@pytest.mark.asyncio
async def test_uncertain_execute_is_not_replayed_and_requests_interaction(
    tmp_path: Path,
) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread("workspace_1")
    turn = _turn(conversation, thread.id)
    state = _state(turn)
    state["pending_tool_calls"] = [
        {"call_id": "call_1", "name": "execute", "arguments_json": "{}"}
    ]
    checkpoints = RecoveryCheckpoints()
    checkpoints.states[turn.id] = state
    sink = CollectingEventSink()
    emitter = EventEmitter(
        session_id="session_1",
        workspace_key="workspace_1",
        sink=sink,
    )
    coordinator = TurnCoordinator(
        workspace_key="workspace_1",
        conversation=conversation,
        config_resolver=lambda thread: cast(Any, None),
        graph=cast(Any, UnusedGraph()),
        runtime_context_factory=lambda turn, operation, projector: cast(
            AgentRuntimeContext, object()
        ),
        operations=OperationController(emitter),
        emitter=emitter,
        checkpoints=checkpoints,
        seal_changes=lambda turn_id: None,
    )

    [result] = await coordinator.reconcile_startup()

    assert result.status is RecoveryStatus.INTERACTION_REQUIRED
    assert checkpoints.deleted == []
    assert conversation.read_thread(thread.id).turns[0].status is TurnStatus.IN_PROGRESS
    assert [event.event_type for event in sink.events] == [
        EventType.INTERACTION_REQUIRED
    ]
    payload = sink.events[0].payload
    assert tuple(choice.decision for choice in payload.choices) == (  # type: ignore[union-attr]
        "retry",
        "abort",
    )
