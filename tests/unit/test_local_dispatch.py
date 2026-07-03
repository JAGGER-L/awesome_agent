from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    DispatchStatus,
    ExecutionKind,
    RunIntent,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.local_dispatch import LocalRunDispatcher
from awesome_agent.persistence.local_runtime import LocalRuntimeRepository
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE


@pytest.mark.asyncio
async def test_local_dispatcher_claims_queued_conversation_run(
    tmp_path: Path,
) -> None:
    runtime = LocalRuntimeRepository(tmp_path / "state.db")
    dispatcher = LocalRunDispatcher(runtime)
    run = _run(tmp_path)
    await runtime.create_run(run, _leader(run))

    lease = await dispatcher.claim_next(
        worker_id=uuid4(),
        worker_name="local-worker",
        lease_duration=timedelta(seconds=30),
        max_attempts=3,
        execution_kinds=frozenset({ExecutionKind.CONVERSATION}),
        runtime_routes=frozenset({CONVERSATION_TURN_ROUTE}),
    )

    assert lease is not None
    claimed = await runtime.get_run(run.id)
    assert claimed.dispatch_status is DispatchStatus.CLAIMED
    runtime.close()


@pytest.mark.asyncio
async def test_local_dispatcher_completes_execution(tmp_path: Path) -> None:
    runtime = LocalRuntimeRepository(tmp_path / "state.db")
    dispatcher = LocalRunDispatcher(runtime)
    run = _run(tmp_path)
    await runtime.create_run(run, _leader(run))
    lease = await dispatcher.claim_next(
        worker_id=uuid4(),
        worker_name="local-worker",
        lease_duration=timedelta(seconds=30),
        max_attempts=3,
    )
    assert lease is not None

    await dispatcher.start_execution(lease, runtime_route=CONVERSATION_TURN_ROUTE)
    await dispatcher.complete_execution(
        lease,
        result_summary="Conversation completed.",
        completion_kind="conversation",
        goal_executed=True,
        result_text="done",
    )

    completed = await runtime.get_run(run.id)
    assert completed.status is RunStatus.COMPLETED
    assert completed.dispatch_status is DispatchStatus.TERMINAL
    assert completed.result_text == "done"
    runtime.close()


@pytest.mark.asyncio
async def test_local_dispatcher_marks_expired_active_run_recovery_required(
    tmp_path: Path,
) -> None:
    runtime = LocalRuntimeRepository(tmp_path / "state.db")
    dispatcher = LocalRunDispatcher(runtime)
    run = _run(tmp_path)
    await runtime.create_run(run, _leader(run))
    lease = await dispatcher.claim_next(
        worker_id=uuid4(),
        worker_name="local-worker",
        lease_duration=timedelta(milliseconds=1),
        max_attempts=3,
    )
    assert lease is not None

    recovered = await dispatcher.recover_expired(max_attempts=3)

    assert recovered == 1
    stored = await runtime.get_run(run.id)
    assert stored.status is RunStatus.RECOVERY_REQUIRED
    assert stored.dispatch_status is DispatchStatus.TERMINAL
    runtime.close()


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
