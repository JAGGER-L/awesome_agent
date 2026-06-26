from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from awesome_agent.domain.enums import AgentKind, ExecutionKind, RunIntent
from awesome_agent.domain.models import Agent, Run, RunLease, RuntimeEvent
from awesome_agent.runtime.dispatch import (
    ApprovalInterrupt,
    CorruptRuntimeStateError,
    PermanentExecutionError,
)
from awesome_agent.runtime.graphs import (
    MODIFYING_CODING_GRAPH,
    MODIFYING_CODING_VERSION,
)
from awesome_agent.runtime.probe_graph import RuntimeProbeState
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig


class FakeRepository:
    def __init__(self, run: Run, agents: list[Agent] | None = None) -> None:
        self.run = run
        self.agents = agents or []

    async def get_run(self, _: UUID) -> Run:
        return self.run

    async def list_agents(self, _: UUID) -> list[Any]:
        return self.agents


class FakeDispatcher:
    def __init__(self, lease: RunLease | None) -> None:
        self.lease = lease
        self.calls: list[tuple[str, object]] = []
        self.cancel_requested = False

    async def claim_next(self, **kwargs: object) -> RunLease | None:
        self.calls.append(("claim", kwargs))
        lease, self.lease = self.lease, None
        return lease

    async def heartbeat(self, lease: RunLease, **_: object) -> RunLease:
        self.calls.append(("heartbeat", lease))
        return lease

    async def start_execution(self, lease: RunLease, **_: object) -> None:
        self.calls.append(("start", lease))

    async def complete_execution(self, lease: RunLease, **kwargs: object) -> None:
        self.calls.append(("complete", kwargs))

    async def release_for_retry(self, lease: RunLease, **kwargs: object) -> None:
        self.calls.append(("retry", kwargs))

    async def request_cancellation(
        self,
        *,
        run_id: UUID,
        requested_by: str | None,
        reason: str | None,
    ) -> RuntimeEvent | None:
        self.calls.append(
            (
                "request_cancellation",
                {
                    "run_id": run_id,
                    "requested_by": requested_by,
                    "reason": reason,
                },
            )
        )
        return None

    async def is_cancel_requested(self, lease: RunLease) -> bool:
        self.calls.append(("cancel_check", lease))
        return self.cancel_requested

    async def mark_cancelled(self, lease: RunLease, **kwargs: object) -> None:
        self.calls.append(("cancelled", kwargs))

    async def release_for_approval_wait(
        self,
        lease: RunLease,
        **kwargs: object,
    ) -> None:
        self.calls.append(("approval_wait", kwargs))

    async def requeue_after_approval(self, **kwargs: object) -> None:
        self.calls.append(("approval_requeue", kwargs))

    async def expire_pending_approvals(self, **kwargs: object) -> int:
        self.calls.append(("expire_approvals", kwargs))
        return 0

    async def mark_recovery_required(self, lease: RunLease, **kwargs: object) -> None:
        self.calls.append(("recovery", kwargs))

    async def fail_execution(self, lease: RunLease, **kwargs: object) -> None:
        self.calls.append(("failed", kwargs))

    async def recover_expired(self, **kwargs: object) -> int:
        self.calls.append(("recover_expired", kwargs))
        return 0

    async def append_fenced_event(self, *_: object, **__: object) -> Any:
        raise NotImplementedError

    async def release_to_queue(self, *_: object, **__: object) -> None:
        raise NotImplementedError


class FakeGraph:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def execute(self, _: Run) -> tuple[RuntimeProbeState, bool]:
        if self.error is not None:
            raise self.error
        return (
            {
                "run_id": "run",
                "graph_name": "runtime-probe",
                "graph_version": 1,
                "phase": "completed",
                "completed_steps": ["initialize", "checkpoint_probe", "finalize"],
                "result_summary": "done",
            },
            False,
        )


class SlowGraph:
    async def execute(self, _: Run) -> tuple[RuntimeProbeState, bool]:
        await asyncio.sleep(60)
        raise AssertionError("slow graph should be cancelled")


class FakeModifyingGraph:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def execute(
        self,
        _: Run,
        __: Agent,
        *,
        event_sink: object | None = None,
    ) -> tuple[dict[str, object], bool]:
        if self.error is not None:
            raise self.error
        return (
            {
                "run_id": "run",
                "agent_id": "agent",
                "graph_name": MODIFYING_CODING_GRAPH,
                "graph_version": MODIFYING_CODING_VERSION,
                "messages": [],
                "model_turn_count": 1,
                "tool_call_count": 2,
                "successful_writes": 1,
                "final_diff_after_write": True,
                "phase": "completed",
                "final_answer": "Changed README.md; validation not run.",
                "result_summary": "modifying done",
            },
            False,
        )


def _lease() -> RunLease:
    now = datetime.now(UTC)
    return RunLease(
        run_id=uuid4(),
        worker_id=uuid4(),
        worker_name="worker",
        fencing_token=1,
        attempt=1,
        lease_acquired_at=now,
        lease_expires_at=now + timedelta(seconds=60),
        heartbeat_at=now,
    )


def _run(lease: RunLease) -> Run:
    return Run(
        id=lease.run_id,
        goal="probe",
        execution_kind=ExecutionKind.RUNTIME_PROBE,
        graph_name="runtime-probe",
        graph_version=1,
        graph_thread_id=f"run:{lease.run_id}",
    )


def _modifying_run(lease: RunLease) -> Run:
    return Run(
        id=lease.run_id,
        goal="modify",
        intent=RunIntent.MODIFYING,
        execution_kind=ExecutionKind.CODING,
        graph_name=MODIFYING_CODING_GRAPH,
        graph_version=MODIFYING_CODING_VERSION,
        graph_thread_id=f"run:{lease.run_id}",
    )


def _config() -> WorkerConfig:
    return WorkerConfig(
        lease_duration=timedelta(seconds=60),
        heartbeat_interval=timedelta(seconds=15),
        poll_interval=0.01,
        recovery_interval=15,
        shutdown_grace=0.01,
        retry_delay=timedelta(seconds=5),
        max_attempts=3,
    )


@pytest.mark.asyncio
async def test_worker_claims_and_completes_probe() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_run(lease)),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        config=_config(),
    )

    assert await worker.run_once()
    assert [call[0] for call in dispatcher.calls] == [
        "claim",
        "start",
        "complete",
    ]


@pytest.mark.asyncio
async def test_worker_retries_transient_graph_error() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_run(lease)),  # type: ignore[arg-type]
        probe_graph=FakeGraph(RuntimeError("temporary")),  # type: ignore[arg-type]
        config=_config(),
    )

    await worker.run_once()

    assert dispatcher.calls[-1][0] == "retry"


@pytest.mark.asyncio
async def test_worker_marks_active_run_cancelled() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    dispatcher.cancel_requested = True
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_run(lease)),  # type: ignore[arg-type]
        probe_graph=SlowGraph(),  # type: ignore[arg-type]
        config=_config(),
    )

    assert await worker.run_once()
    calls = [call[0] for call in dispatcher.calls]

    assert "cancelled" in calls
    assert "complete" not in calls
    assert "retry" not in calls


@pytest.mark.asyncio
async def test_worker_marks_corrupt_state_for_recovery() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_run(lease)),  # type: ignore[arg-type]
        probe_graph=FakeGraph(CorruptRuntimeStateError("corrupt")),  # type: ignore[arg-type]
        config=_config(),
    )

    await worker.run_once()

    assert dispatcher.calls[-1][0] == "recovery"


@pytest.mark.asyncio
async def test_worker_fails_permanent_graph_error() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_run(lease)),  # type: ignore[arg-type]
        probe_graph=FakeGraph(PermanentExecutionError("permanent")),  # type: ignore[arg-type]
        config=_config(),
    )

    await worker.run_once()

    assert dispatcher.calls[-1][0] == "failed"


@pytest.mark.asyncio
async def test_worker_forever_recovers_and_stops() -> None:
    dispatcher = FakeDispatcher(None)

    async def stop_sleep(_: float) -> None:
        worker.request_stop()
        await asyncio.sleep(0)

    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(Run(goal="unused")),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        config=_config(),
        sleep=stop_sleep,
    )

    await worker.run_forever()

    assert [call[0] for call in dispatcher.calls] == [
        "recover_expired",
        "expire_approvals",
        "claim",
    ]


@pytest.mark.asyncio
async def test_worker_claims_modifying_graph_when_configured() -> None:
    dispatcher = FakeDispatcher(None)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(Run(goal="unused")),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        modifying_graph=object(),  # type: ignore[arg-type]
        config=_config(),
    )

    assert not await worker.run_once()
    claim = dispatcher.calls[0][1]

    assert isinstance(claim, dict)
    assert (
        MODIFYING_CODING_GRAPH,
        MODIFYING_CODING_VERSION,
    ) in claim["graph_identities"]


@pytest.mark.asyncio
async def test_worker_executes_modifying_graph_with_unvalidated_completion() -> None:
    lease = _lease()
    run = _modifying_run(lease)
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(run, [leader]),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        modifying_graph=FakeModifyingGraph(),  # type: ignore[arg-type]
        config=_config(),
    )

    assert await worker.run_once()
    complete = dispatcher.calls[-1]

    assert complete[0] == "complete"
    assert isinstance(complete[1], dict)
    assert complete[1]["completion_kind"] == "modifying_unvalidated"
    assert complete[1]["result_text"] == "Changed README.md; validation not run."


@pytest.mark.asyncio
async def test_worker_releases_modifying_run_for_approval_wait() -> None:
    lease = _lease()
    run = _modifying_run(lease)
    approval_id = uuid4()
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(run, [leader]),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        modifying_graph=FakeModifyingGraph(ApprovalInterrupt(approval_id)),  # type: ignore[arg-type]
        config=_config(),
    )

    assert await worker.run_once()
    release = dispatcher.calls[-1]

    assert release[0] == "approval_wait"
    assert isinstance(release[1], dict)
    assert release[1]["approval_id"] == approval_id
    assert release[1]["reason"] == "approval_wait"


@pytest.mark.asyncio
async def test_worker_marks_coding_run_without_leader_for_recovery() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_modifying_run(lease)),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        modifying_graph=FakeModifyingGraph(),  # type: ignore[arg-type]
        config=_config(),
    )

    await worker.run_once()

    assert dispatcher.calls[-1][0] == "recovery"


@pytest.mark.asyncio
async def test_worker_rejects_unconfigured_coding_graph() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_modifying_run(lease)),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        config=_config(),
    )

    await worker.run_once()

    assert dispatcher.calls[-1][0] == "recovery"
