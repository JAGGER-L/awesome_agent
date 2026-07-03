from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.local_runtime import LocalRuntimeRepository
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE


@pytest.mark.asyncio
async def test_local_runtime_repository_persists_run_agent_and_events(
    tmp_path: Path,
) -> None:
    repository = LocalRuntimeRepository(tmp_path / "state.db")
    run = _run(tmp_path)
    leader = _leader(run)

    await repository.create_run(run, leader)
    event = await repository.append_event(
        run_id=run.id,
        event_type=EventType.RUN_CREATED,
        payload={"thread_id": "thread-1", "goal": run.goal},
        agent_id=leader.id,
    )
    repository.close()

    reopened = LocalRuntimeRepository(tmp_path / "state.db")
    assert await reopened.get_run(run.id) == run
    assert await reopened.list_agents(run.id) == [leader]
    assert (await reopened.list_events(run.id))[0] == event
    reopened.close()


@pytest.mark.asyncio
async def test_local_runtime_repository_updates_run_and_lists_created_order(
    tmp_path: Path,
) -> None:
    repository = LocalRuntimeRepository(tmp_path / "state.db")
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    first = _run(tmp_path, goal="first").model_copy(update={"created_at": created_at})
    second = _run(tmp_path, goal="second").model_copy(
        update={"created_at": created_at + timedelta(seconds=1)}
    )
    await repository.create_run(first, _leader(first))
    await repository.create_run(second, _leader(second))

    completed = first.model_copy(
        update={
            "status": RunStatus.COMPLETED,
            "dispatch_status": DispatchStatus.TERMINAL,
            "result_text": "done",
        }
    )
    await repository.update_run(completed)

    assert await repository.get_run(first.id) == completed
    assert [run.id for run in await repository.list_runs()] == [first.id, second.id]
    repository.close()


@pytest.mark.asyncio
async def test_local_runtime_repository_cancel_queued_run(tmp_path: Path) -> None:
    repository = LocalRuntimeRepository(tmp_path / "state.db")
    run = _run(tmp_path)
    await repository.create_run(run, _leader(run))

    cancelled, event = await repository.cancel_run(run.id)

    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.dispatch_status is DispatchStatus.TERMINAL
    assert event is not None
    assert event.payload["status"] == "cancelled"
    assert (await repository.get_run(run.id)).status is RunStatus.CANCELLED
    repository.close()


@pytest.mark.asyncio
async def test_local_runtime_repository_does_not_cancel_terminal_run(
    tmp_path: Path,
) -> None:
    repository = LocalRuntimeRepository(tmp_path / "state.db")
    run = _run(tmp_path).model_copy(
        update={
            "status": RunStatus.COMPLETED,
            "dispatch_status": DispatchStatus.TERMINAL,
        }
    )
    await repository.create_run(run, _leader(run))

    unchanged, event = await repository.cancel_run(run.id)

    assert unchanged == run
    assert event is None
    assert await repository.get_run(run.id) == run
    repository.close()


@pytest.mark.asyncio
async def test_local_runtime_repository_persists_transition_id_idempotently(
    tmp_path: Path,
) -> None:
    repository = LocalRuntimeRepository(tmp_path / "state.db")
    run = _run(tmp_path)
    await repository.create_run(run, _leader(run))

    first = await repository.append_event(
        run_id=run.id,
        event_type=EventType.RUN_STATUS_CHANGED,
        payload={"status": "running"},
        transition_id="transition-1",
    )
    repeated = await repository.append_event(
        run_id=run.id,
        event_type=EventType.RUN_STATUS_CHANGED,
        payload={"status": "running"},
        transition_id="transition-1",
    )

    assert repeated == first
    assert await repository.list_events(run.id) == [first]
    assert (await repository.list_events(run.id))[0].transition_id == "transition-1"
    repository.close()


@pytest.mark.asyncio
async def test_local_runtime_repository_redacts_event_payload(
    tmp_path: Path,
) -> None:
    repository = LocalRuntimeRepository(tmp_path / "state.db")
    run = _run(tmp_path)
    leader = _leader(run)
    await repository.create_run(run, leader)

    event = await repository.append_event(
        run_id=run.id,
        event_type=EventType.TOOL_CALL_CREATED,
        payload={"env": "DEEPSEEK_API_KEY=sk-secret-value"},
        agent_id=leader.id,
    )

    assert "sk-secret-value" not in str(event.payload)
    assert "sk-secret-value" not in str(
        (await repository.list_events(run.id))[0].payload
    )
    repository.close()


def _run(tmp_path: Path, *, goal: str = "hello") -> Run:
    return Run(
        goal=goal,
        intent=RunIntent.CONVERSATION,
        execution_kind=ExecutionKind.CONVERSATION,
        runtime_route=CONVERSATION_TURN_ROUTE,
        status=RunStatus.CREATED,
        dispatch_status=DispatchStatus.QUEUED,
        working_directory=tmp_path,
    )


def _leader(run: Run) -> Agent:
    return Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
        status=AgentStatus.READY,
    )
