from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import DispatchStatus, EventType, RunStatus
from awesome_agent.domain.models import RunLease
from awesome_agent.persistence.dispatch import PostgresRunDispatcher
from awesome_agent.persistence.models import RunRecord
from awesome_agent.runtime.dispatch import LeaseLost


class ScalarRows:
    def __init__(self, values: list[RunRecord]) -> None:
        self.values = values

    def __iter__(self) -> Any:
        return iter(self.values)


class FakeSession:
    def __init__(
        self,
        *,
        scalar_results: list[object],
        rows: list[RunRecord] | None = None,
    ) -> None:
        self.scalar_results = scalar_results
        self.rows = rows or []
        self.added: list[object] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    async def scalar(self, _: object) -> object:
        return self.scalar_results.pop(0)

    async def scalars(self, _: object) -> ScalarRows:
        return ScalarRows(self.rows)

    def add(self, value: object) -> None:
        self.added.append(value)


class FakeFactory:
    def __init__(self, sessions: list[FakeSession]) -> None:
        self.sessions = sessions

    def begin(self) -> FakeSession:
        return self.sessions.pop(0)


def _record(
    *,
    status: DispatchStatus = DispatchStatus.QUEUED,
    attempt: int = 0,
) -> RunRecord:
    now = datetime.now(UTC)
    return RunRecord(
        id=uuid4(),
        goal="fixture",
        mode="solo",
        status=RunStatus.CREATED.value,
        repository_id=None,
        base_commit=None,
        intent="modifying",
        dispatch_status=status.value,
        available_at=now,
        current_worker_id=None,
        current_worker_name=None,
        fencing_token=0,
        attempt=attempt,
        lease_acquired_at=None,
        lease_expires_at=None,
        heartbeat_at=None,
        last_release_reason=None,
        last_dispatch_error=None,
        workspace_path=None,
        integration_branch=None,
        workspace_state=None,
        graph_thread_id=None,
        legacy=False,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_claim_heartbeat_and_fenced_event() -> None:
    now = datetime.now(UTC)
    record = _record()
    worker_id = uuid4()
    factory = FakeFactory(
        [
            FakeSession(scalar_results=[now, record, 2]),
            FakeSession(scalar_results=[now + timedelta(seconds=10), record]),
            FakeSession(scalar_results=[now + timedelta(seconds=11), record, 3]),
        ]
    )
    dispatcher = PostgresRunDispatcher(factory)  # type: ignore[arg-type]

    lease = await dispatcher.claim_next(
        worker_id=worker_id,
        worker_name="worker",
        lease_duration=timedelta(seconds=60),
        max_attempts=3,
    )
    assert lease is not None
    assert lease.fencing_token == 1
    renewed = await dispatcher.heartbeat(
        lease,
        lease_duration=timedelta(seconds=60),
    )
    event = await dispatcher.append_fenced_event(
        renewed,
        event_type=EventType.RUN_STATUS_CHANGED,
        payload={"status": "created"},
    )
    assert event.sequence == 4


@pytest.mark.asyncio
async def test_release_retry_and_attempt_exhaustion() -> None:
    now = datetime.now(UTC)
    retry_record = _record(status=DispatchStatus.CLAIMED, attempt=1)
    retry_record.current_worker_id = uuid4()
    retry_record.current_worker_name = "worker"
    retry_record.fencing_token = 1
    retry_record.lease_acquired_at = now
    retry_record.lease_expires_at = now + timedelta(seconds=60)
    retry_record.heartbeat_at = now
    exhausted = _record(status=DispatchStatus.CLAIMED, attempt=3)
    exhausted.current_worker_id = uuid4()
    exhausted.current_worker_name = "worker"
    exhausted.fencing_token = 3
    exhausted.lease_acquired_at = now
    exhausted.lease_expires_at = now + timedelta(seconds=60)
    exhausted.heartbeat_at = now
    factory = FakeFactory(
        [
            FakeSession(scalar_results=[now, retry_record, 0]),
            FakeSession(scalar_results=[now, exhausted, 0]),
        ]
    )
    dispatcher = PostgresRunDispatcher(factory)  # type: ignore[arg-type]

    await dispatcher.release_for_retry(
        _lease_from(retry_record),
        delay=timedelta(seconds=30),
        reason="retry",
        max_attempts=3,
    )
    assert retry_record.dispatch_status == DispatchStatus.RETRY_SCHEDULED.value
    await dispatcher.release_to_queue(
        _lease_from(exhausted),
        reason="shutdown",
        max_attempts=3,
    )
    assert exhausted.status == RunStatus.RECOVERY_REQUIRED.value
    assert exhausted.dispatch_status == DispatchStatus.TERMINAL.value


@pytest.mark.asyncio
async def test_recovery_requeues_then_terminates() -> None:
    now = datetime.now(UTC)
    retry = _record(status=DispatchStatus.CLAIMED, attempt=1)
    terminal = _record(status=DispatchStatus.EXECUTING, attempt=3)
    for record in (retry, terminal):
        record.current_worker_id = uuid4()
        record.current_worker_name = "worker"
        record.fencing_token = record.attempt
        record.lease_acquired_at = now - timedelta(seconds=120)
        record.lease_expires_at = now - timedelta(seconds=60)
        record.heartbeat_at = now - timedelta(seconds=120)
    session = FakeSession(
        scalar_results=[now, 0, 0],
        rows=[retry, terminal],
    )
    dispatcher = PostgresRunDispatcher(FakeFactory([session]))  # type: ignore[arg-type]

    assert await dispatcher.recover_expired(max_attempts=3) == 2
    assert retry.dispatch_status == DispatchStatus.QUEUED.value
    assert terminal.status == RunStatus.RECOVERY_REQUIRED.value


@pytest.mark.asyncio
async def test_invalid_or_expired_lease_is_rejected() -> None:
    now = datetime.now(UTC)
    record = _record(status=DispatchStatus.CLAIMED, attempt=1)
    record.current_worker_id = uuid4()
    record.current_worker_name = "worker"
    record.fencing_token = 1
    record.lease_acquired_at = now - timedelta(seconds=120)
    record.lease_expires_at = now - timedelta(seconds=1)
    record.heartbeat_at = now - timedelta(seconds=120)
    dispatcher = PostgresRunDispatcher(
        FakeFactory([FakeSession(scalar_results=[now, record])])  # type: ignore[arg-type]
    )

    with pytest.raises(LeaseLost):
        await dispatcher.heartbeat(
            _lease_from(record),
            lease_duration=timedelta(seconds=60),
        )


@pytest.mark.asyncio
async def test_graph_projection_transitions_are_fenced() -> None:
    now = datetime.now(UTC)
    record = _record(status=DispatchStatus.CLAIMED, attempt=1)
    record.current_worker_id = uuid4()
    record.current_worker_name = "worker"
    record.fencing_token = 1
    record.lease_acquired_at = now
    record.lease_expires_at = now + timedelta(seconds=60)
    record.heartbeat_at = now
    record.graph_name = "runtime-probe"
    record.graph_version = 1
    factory = FakeFactory(
        [
            FakeSession(scalar_results=[now, record, 0]),
            FakeSession(scalar_results=[now, record, 1]),
        ]
    )
    dispatcher = PostgresRunDispatcher(factory)  # type: ignore[arg-type]
    lease = _lease_from(record)

    await dispatcher.start_execution(
        lease,
        graph_name="runtime-probe",
        graph_version=1,
    )
    assert record.status == RunStatus.RUNNING.value
    assert record.dispatch_status == DispatchStatus.EXECUTING.value
    await dispatcher.complete_execution(
        lease,
        result_summary="probe complete",
    )
    assert record.status == RunStatus.COMPLETED.value
    assert record.dispatch_status == DispatchStatus.TERMINAL.value
    assert record.current_worker_id is None


@pytest.mark.asyncio
async def test_permanent_execution_failure_requires_recovery() -> None:
    now = datetime.now(UTC)
    record = _record(status=DispatchStatus.EXECUTING, attempt=1)
    record.current_worker_id = uuid4()
    record.current_worker_name = "worker"
    record.fencing_token = 1
    record.lease_acquired_at = now
    record.lease_expires_at = now + timedelta(seconds=60)
    record.heartbeat_at = now
    dispatcher = PostgresRunDispatcher(
        FakeFactory([FakeSession(scalar_results=[now, record, 0])])  # type: ignore[arg-type]
    )

    await dispatcher.mark_recovery_required(
        _lease_from(record),
        reason="unsupported graph",
    )

    assert record.status == RunStatus.RECOVERY_REQUIRED.value
    assert record.dispatch_status == DispatchStatus.TERMINAL.value


def _lease_from(record: RunRecord) -> RunLease:
    assert record.current_worker_id is not None
    assert record.current_worker_name is not None
    assert record.lease_acquired_at is not None
    assert record.lease_expires_at is not None
    assert record.heartbeat_at is not None
    return RunLease(
        run_id=record.id,
        worker_id=record.current_worker_id,
        worker_name=record.current_worker_name,
        fencing_token=record.fencing_token,
        attempt=record.attempt,
        lease_acquired_at=record.lease_acquired_at,
        lease_expires_at=record.lease_expires_at,
        heartbeat_at=record.heartbeat_at,
    )
