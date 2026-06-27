from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    ApprovalStatus,
    DispatchStatus,
    EventType,
    RunStatus,
    TodoStatus,
)
from awesome_agent.domain.models import RunLease
from awesome_agent.persistence.dispatch import PostgresRunDispatcher
from awesome_agent.persistence.models import (
    AgentRecord,
    ApprovalRecord,
    RunRecord,
    RuntimeEventRecord,
    TodoRecord,
)
from awesome_agent.runtime.dispatch import (
    ApprovalInterrupt,
    DispatchConflict,
    LeaseLost,
)


class ScalarRows:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def __iter__(self) -> Any:
        return iter(self.values)


class FakeSession:
    def __init__(
        self,
        *,
        scalar_results: list[object],
        rows: list[object] | None = None,
        rows_by_table: dict[str, list[object]] | None = None,
    ) -> None:
        self.scalar_results = scalar_results
        self.rows = rows or []
        self.rows_by_table = rows_by_table or {}
        self.added: list[object] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    async def scalar(self, statement: object) -> object:
        if self.scalar_results:
            return self.scalar_results.pop(0)
        rendered = str(statement)
        if "max(runtime_events.sequence)" in rendered:
            events = _added_events(self)
            return max((event.sequence for event in events), default=0)
        if "runtime_events.transition_id" in rendered:
            return None
        return None

    async def get(
        self,
        _: object,
        __: object,
        **___: object,
    ) -> object:
        return self.scalar_results.pop(0)

    async def scalars(self, statement: object) -> ScalarRows:
        rendered = str(statement)
        for table, rows in self.rows_by_table.items():
            if f"FROM {table}" in rendered:
                return ScalarRows(rows)
        if "FROM agents" in rendered or "FROM todos" in rendered:
            return ScalarRows([])
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
        cancel_requested_at=None,
        cancel_requested_by=None,
        cancel_reason=None,
        workspace_path=None,
        integration_branch=None,
        workspace_state=None,
        graph_thread_id=None,
        legacy=False,
        created_at=now,
        updated_at=now,
    )


def _approval(record: RunRecord, *, status: ApprovalStatus) -> ApprovalRecord:
    now = datetime.now(UTC)
    return ApprovalRecord(
        id=uuid4(),
        run_id=record.id,
        agent_id=None,
        tool_invocation_id=uuid4(),
        tool_call_id="call",
        tool_name="shell.execute",
        tool_version="1",
        canonical_arguments={"argv": ["python", "script.py"]},
        arguments_hash="hash",
        workspace_path="workspace",
        workspace_fingerprint="fingerprint",
        capabilities=["shell:execute"],
        risk_level="medium",
        status=status.value,
        expires_at=now - timedelta(seconds=1),
        decided_at=None,
        decided_by=None,
        decision_reason=None,
        created_at=now,
        updated_at=now,
    )


def _leader_record(run_id: object) -> AgentRecord:
    now = datetime.now(UTC)
    return AgentRecord(
        id=uuid4(),
        run_id=run_id,
        parent_agent_id=None,
        kind=AgentKind.LEADER.value,
        profile="leader",
        model="fake",
        status=AgentStatus.RUNNING.value,
        revision=1,
        created_at=now,
        updated_at=now,
    )


def _todo_record(run_id: object, *, status: TodoStatus) -> TodoRecord:
    now = datetime.now(UTC)
    return TodoRecord(
        id=uuid4(),
        run_id=run_id,
        parent_id=None,
        title="Task",
        description="",
        status=status.value,
        primary_owner_id=None,
        collaborator_ids=[],
        acceptance_criteria=[],
        blocker=None,
        revision=1,
        created_at=now,
        updated_at=now,
    )


def _added_events(session: FakeSession) -> list[RuntimeEventRecord]:
    return [item for item in session.added if isinstance(item, RuntimeEventRecord)]


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
async def test_release_for_approval_wait_pauses_and_clears_lease() -> None:
    now = datetime.now(UTC)
    record = _record(status=DispatchStatus.EXECUTING, attempt=1)
    record.status = RunStatus.RUNNING.value
    record.current_worker_id = uuid4()
    record.current_worker_name = "worker"
    record.fencing_token = 1
    record.lease_acquired_at = now
    record.lease_expires_at = now + timedelta(seconds=60)
    record.heartbeat_at = now
    dispatcher = PostgresRunDispatcher(
        FakeFactory([FakeSession(scalar_results=[now, record, 0, 1])])  # type: ignore[arg-type]
    )

    await dispatcher.release_for_approval_wait(
        _lease_from(record),
        approval_id=uuid4(),
        reason="approval_wait",
    )

    assert record.status == RunStatus.PAUSED.value
    assert record.dispatch_status == DispatchStatus.WAITING.value
    assert record.current_worker_id is None
    assert record.attempt == 1


@pytest.mark.asyncio
async def test_requeue_after_approval_makes_waiting_run_claimable() -> None:
    now = datetime.now(UTC)
    record = _record(status=DispatchStatus.WAITING, attempt=1)
    record.status = RunStatus.PAUSED.value
    approval = _approval(record, status=ApprovalStatus.APPROVED)
    dispatcher = PostgresRunDispatcher(
        FakeFactory([FakeSession(scalar_results=[now, approval, record, 0, 1])])  # type: ignore[arg-type]
    )

    await dispatcher.requeue_after_approval(
        run_id=record.id,
        approval_id=approval.id,
        reason="approval_decided",
    )

    assert record.status == RunStatus.RUNNING.value
    assert record.dispatch_status == DispatchStatus.QUEUED.value
    assert record.available_at == now


@pytest.mark.asyncio
async def test_expire_pending_approvals_requeues_waiting_run() -> None:
    now = datetime.now(UTC)
    record = _record(status=DispatchStatus.WAITING, attempt=1)
    record.status = RunStatus.PAUSED.value
    approval = _approval(record, status=ApprovalStatus.PENDING)
    session = FakeSession(
        scalar_results=[now, record, None, 0, 1],
        rows=[approval],
    )
    dispatcher = PostgresRunDispatcher(FakeFactory([session]))  # type: ignore[arg-type]

    assert await dispatcher.expire_pending_approvals() == 1

    assert approval.status == ApprovalStatus.EXPIRED.value
    assert record.status == RunStatus.RUNNING.value
    assert record.dispatch_status == DispatchStatus.QUEUED.value


@pytest.mark.asyncio
async def test_request_cancellation_cancels_queued_run() -> None:
    now = datetime.now(UTC)
    record = _record(status=DispatchStatus.QUEUED, attempt=0)
    dispatcher = PostgresRunDispatcher(
        FakeFactory([FakeSession(scalar_results=[now, record, None, 0])])  # type: ignore[arg-type]
    )

    event = await dispatcher.request_cancellation(
        run_id=record.id,
        requested_by="api",
        reason="user requested",
    )

    assert event is not None
    assert record.status == RunStatus.CANCELLED.value
    assert record.dispatch_status == DispatchStatus.TERMINAL.value
    assert record.cancel_requested_at == now
    assert record.cancel_requested_by == "api"
    assert record.cancel_reason == "user requested"


@pytest.mark.asyncio
async def test_request_cancellation_cancels_waiting_approval_run() -> None:
    now = datetime.now(UTC)
    record = _record(status=DispatchStatus.WAITING, attempt=1)
    record.status = RunStatus.PAUSED.value
    approval = _approval(record, status=ApprovalStatus.PENDING)
    dispatcher = PostgresRunDispatcher(
        FakeFactory(
            [
                FakeSession(
                    scalar_results=[now, record, None, 0, None, 1],
                    rows=[approval],
                )
            ]
        )  # type: ignore[arg-type]
    )

    await dispatcher.request_cancellation(
        run_id=record.id,
        requested_by="api",
        reason=None,
    )

    assert record.status == RunStatus.CANCELLED.value
    assert record.dispatch_status == DispatchStatus.TERMINAL.value
    assert approval.status == ApprovalStatus.DENIED.value
    assert approval.decision_reason == "run_cancelled"


@pytest.mark.asyncio
async def test_request_cancellation_records_active_run_signal() -> None:
    now = datetime.now(UTC)
    record = _record(status=DispatchStatus.EXECUTING, attempt=1)
    record.status = RunStatus.RUNNING.value
    record.current_worker_id = uuid4()
    record.current_worker_name = "worker"
    record.fencing_token = 1
    record.lease_acquired_at = now
    record.lease_expires_at = now + timedelta(seconds=60)
    record.heartbeat_at = now
    dispatcher = PostgresRunDispatcher(
        FakeFactory([FakeSession(scalar_results=[now, record, None, 0])])  # type: ignore[arg-type]
    )

    event = await dispatcher.request_cancellation(
        run_id=record.id,
        requested_by="api",
        reason="stop",
    )

    assert event is not None
    assert event.event_type is EventType.CANCELLATION_REQUESTED
    assert record.status == RunStatus.RUNNING.value
    assert record.dispatch_status == DispatchStatus.EXECUTING.value
    assert record.cancel_requested_at == now


@pytest.mark.asyncio
async def test_terminal_run_cancellation_is_rejected() -> None:
    now = datetime.now(UTC)
    record = _record(status=DispatchStatus.TERMINAL, attempt=1)
    record.status = RunStatus.COMPLETED.value
    dispatcher = PostgresRunDispatcher(
        FakeFactory([FakeSession(scalar_results=[now, record])])  # type: ignore[arg-type]
    )

    with pytest.raises(DispatchConflict):
        await dispatcher.request_cancellation(
            run_id=record.id,
            requested_by="api",
            reason=None,
        )


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
async def test_completion_updates_lifecycle_projections_and_events() -> None:
    now = datetime.now(UTC)
    record = _record(status=DispatchStatus.EXECUTING, attempt=1)
    record.status = RunStatus.RUNNING.value
    record.current_worker_id = uuid4()
    record.current_worker_name = "worker"
    record.fencing_token = 1
    record.lease_acquired_at = now
    record.lease_expires_at = now + timedelta(seconds=60)
    record.heartbeat_at = now
    leader = _leader_record(record.id)
    todo = _todo_record(record.id, status=TodoStatus.IN_PROGRESS)
    session = FakeSession(
        scalar_results=[now, record, 0],
        rows_by_table={
            "agents": [leader],
            "todos": [todo],
        },
    )
    dispatcher = PostgresRunDispatcher(FakeFactory([session]))  # type: ignore[arg-type]

    await dispatcher.complete_execution(
        _lease_from(record),
        result_summary="validated",
        completion_kind="modifying_validated",
        goal_executed=True,
        result_text="done",
    )

    assert record.status == RunStatus.COMPLETED.value
    assert record.dispatch_status == DispatchStatus.TERMINAL.value
    assert record.updated_at == now
    assert leader.status == AgentStatus.COMPLETED.value
    assert leader.updated_at == now
    assert leader.revision == 2
    assert todo.status == TodoStatus.DONE.value
    assert todo.updated_at == now
    assert todo.revision == 2
    events = _added_events(session)
    event_types = [event.event_type for event in events]
    assert EventType.RUN_STATUS_CHANGED.value in event_types
    assert EventType.AGENT_STATUS_CHANGED.value in event_types
    assert EventType.TODO_STATUS_CHANGED.value in event_types
    todo_event = next(
        event for event in events if event.event_type == EventType.TODO_STATUS_CHANGED
    )
    assert todo_event.task_id == todo.id
    assert todo_event.payload["previous_status"] == TodoStatus.IN_PROGRESS.value
    assert todo_event.payload["status"] == TodoStatus.DONE.value
    assert todo_event.payload["revision"] == 2


@pytest.mark.asyncio
async def test_failure_updates_lifecycle_projections_and_events() -> None:
    now = datetime.now(UTC)
    record = _record(status=DispatchStatus.EXECUTING, attempt=1)
    record.status = RunStatus.RUNNING.value
    record.current_worker_id = uuid4()
    record.current_worker_name = "worker"
    record.fencing_token = 1
    record.lease_acquired_at = now
    record.lease_expires_at = now + timedelta(seconds=60)
    record.heartbeat_at = now
    leader = _leader_record(record.id)
    todo = _todo_record(record.id, status=TodoStatus.IN_PROGRESS)
    session = FakeSession(
        scalar_results=[now, record, 0],
        rows_by_table={
            "agents": [leader],
            "todos": [todo],
        },
    )
    dispatcher = PostgresRunDispatcher(FakeFactory([session]))  # type: ignore[arg-type]

    await dispatcher.fail_execution(
        _lease_from(record),
        reason="validation failed",
    )

    assert record.status == RunStatus.FAILED.value
    assert record.dispatch_status == DispatchStatus.TERMINAL.value
    assert record.updated_at == now
    assert leader.status == AgentStatus.FAILED.value
    assert leader.updated_at == now
    assert leader.revision == 2
    assert todo.status == TodoStatus.BLOCKED.value
    assert todo.blocker == "validation failed"
    assert todo.updated_at == now
    assert todo.revision == 2
    events = _added_events(session)
    event_types = [event.event_type for event in events]
    assert EventType.RUN_STATUS_CHANGED.value in event_types
    assert EventType.AGENT_STATUS_CHANGED.value in event_types
    assert EventType.TODO_STATUS_CHANGED.value in event_types
    todo_event = next(
        event for event in events if event.event_type == EventType.TODO_STATUS_CHANGED
    )
    assert todo_event.payload["blocker"] == "validation failed"


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


def test_approval_interrupt_preserves_approval_id() -> None:
    """Verify ApprovalInterrupt carries the correct approval id."""

    approval_id = uuid4()
    exc = ApprovalInterrupt(approval_id)

    assert exc.approval_id == approval_id
    assert "approval" in str(exc).lower()
    assert isinstance(exc, RuntimeError)


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
