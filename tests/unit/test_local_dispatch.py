from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

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
from awesome_agent.persistence.local_dispatch import LocalRunDispatcher
from awesome_agent.persistence.local_runtime import LocalRuntimeRepository
from awesome_agent.runtime.dispatch import DispatchConflict, LeaseLost
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
    assert [
        event.event_type for event in await runtime.list_events(run.id)
    ] == [
        EventType.DISPATCH_CLAIMED,
        EventType.RUN_STATUS_CHANGED,
        EventType.GRAPH_STARTED,
        EventType.RUN_STATUS_CHANGED,
        EventType.GRAPH_COMPLETED,
    ]
    runtime.close()


@pytest.mark.asyncio
async def test_local_dispatcher_requeues_expired_run_before_max_attempts(
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
    assert stored.status is RunStatus.CREATED
    assert stored.dispatch_status is DispatchStatus.QUEUED
    assert stored.current_worker_id is None
    assert stored.lease_expires_at is None
    assert (await runtime.list_events(run.id))[-1].event_type is (
        EventType.DISPATCH_LEASE_EXPIRED
    )
    runtime.close()


@pytest.mark.asyncio
async def test_local_dispatcher_marks_expired_run_recovery_required_at_max_attempts(
    tmp_path: Path,
) -> None:
    runtime = LocalRuntimeRepository(tmp_path / "state.db")
    dispatcher = LocalRunDispatcher(runtime)
    run = _run(tmp_path).model_copy(update={"attempt": 2})
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


@pytest.mark.asyncio
async def test_local_dispatcher_cancel_probe_requires_owned_live_lease(
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
    )
    assert lease is not None

    wrong_worker = lease.model_copy(update={"worker_id": uuid4()})
    with pytest.raises(LeaseLost):
        await dispatcher.is_cancel_requested(wrong_worker)

    expired = (await runtime.get_run(run.id)).model_copy(
        update={"lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    await runtime.update_run(expired)
    with pytest.raises(LeaseLost):
        await dispatcher.is_cancel_requested(lease)
    runtime.close()


@pytest.mark.asyncio
async def test_local_dispatcher_rejects_fenced_operation_after_cancelled_terminal(
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
    )
    assert lease is not None

    await dispatcher.mark_cancelled(lease, reason="stop")

    stored = await runtime.get_run(run.id)
    assert stored.dispatch_status is DispatchStatus.TERMINAL
    assert stored.current_worker_id is None
    assert stored.lease_expires_at is None
    with pytest.raises(LeaseLost):
        await dispatcher.append_fenced_event(
            lease,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={"status": "cancelled"},
        )
    runtime.close()


@pytest.mark.asyncio
async def test_local_dispatcher_release_for_approval_wait_pauses_run(
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
    )
    assert lease is not None
    await dispatcher.start_execution(lease, runtime_route=CONVERSATION_TURN_ROUTE)

    await dispatcher.release_for_approval_wait(
        lease,
        approval_id=uuid4(),
        reason="approval_wait",
    )

    stored = await runtime.get_run(run.id)
    assert stored.status is RunStatus.PAUSED
    assert stored.dispatch_status is DispatchStatus.WAITING
    assert stored.current_worker_id is None
    runtime.close()


@pytest.mark.asyncio
async def test_local_dispatcher_does_not_cancel_terminal_run(
    tmp_path: Path,
) -> None:
    runtime = LocalRuntimeRepository(tmp_path / "state.db")
    dispatcher = LocalRunDispatcher(runtime)
    run = _run(tmp_path).model_copy(
        update={
            "status": RunStatus.COMPLETED,
            "dispatch_status": DispatchStatus.TERMINAL,
        }
    )
    await runtime.create_run(run, _leader(run))

    with pytest.raises(DispatchConflict):
        await dispatcher.request_cancellation(
            run_id=run.id,
            requested_by="api",
            reason="stop",
        )

    stored = await runtime.get_run(run.id)
    assert stored.status is RunStatus.COMPLETED
    runtime.close()


@pytest.mark.asyncio
async def test_local_dispatcher_append_fenced_event_is_transition_id_idempotent(
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
    )
    assert lease is not None

    first = await dispatcher.append_fenced_event(
        lease,
        event_type=EventType.RUN_STATUS_CHANGED,
        payload={"status": "running"},
        transition_id="graph-status:1",
    )
    second = await dispatcher.append_fenced_event(
        lease,
        event_type=EventType.RUN_STATUS_CHANGED,
        payload={"status": "running"},
        transition_id="graph-status:1",
    )

    assert second == first
    assert [
        event.transition_id for event in await runtime.list_events(run.id)
    ] == [None, "graph-status:1"]
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
