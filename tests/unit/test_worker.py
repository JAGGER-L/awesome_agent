from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from awesome_agent.domain.enums import ExecutionKind
from awesome_agent.domain.models import Run, RunLease
from awesome_agent.runtime.dispatch import CorruptRuntimeStateError
from awesome_agent.runtime.probe_graph import RuntimeProbeState
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig


class FakeRepository:
    def __init__(self, run: Run) -> None:
        self.run = run

    async def get_run(self, _: UUID) -> Run:
        return self.run

    async def list_agents(self, _: UUID) -> list[Any]:
        return []


class FakeDispatcher:
    def __init__(self, lease: RunLease | None) -> None:
        self.lease = lease
        self.calls: list[tuple[str, object]] = []

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
        "claim",
    ]
