from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.domain.enums import (
    AgentStatus,
    ApprovalStatus,
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunStatus,
    TodoStatus,
)
from awesome_agent.domain.models import RunLease, RuntimeEvent
from awesome_agent.persistence.lifecycle import (
    transition_agents_for_run,
    transition_run_status,
    transition_todos_for_run,
)
from awesome_agent.persistence.models import (
    ApprovalRecord,
    RunRecord,
    RuntimeEventRecord,
)
from awesome_agent.runtime.dispatch import DispatchConflict, LeaseLost, RunDispatcher


class PostgresRunDispatcher(RunDispatcher):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def claim_next(
        self,
        *,
        worker_id: UUID,
        worker_name: str,
        lease_duration: timedelta,
        max_attempts: int,
        execution_kinds: frozenset[ExecutionKind] | None = None,
        run_intents: frozenset[RunIntent] | None = None,
        graph_identities: frozenset[tuple[str, int]] | None = None,
    ) -> RunLease | None:
        if max_attempts < 1:
            raise ValueError("Maximum attempts must be positive.")
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            query = select(RunRecord).where(
                RunRecord.dispatch_status.in_(
                    [
                        DispatchStatus.QUEUED.value,
                        DispatchStatus.RETRY_SCHEDULED.value,
                    ]
                ),
                RunRecord.available_at <= now,
                RunRecord.attempt < max_attempts,
                RunRecord.legacy.is_(False),
            )
            if execution_kinds is not None:
                query = query.where(
                    RunRecord.execution_kind.in_(
                        [kind.value for kind in execution_kinds]
                    )
                )
            if run_intents is not None:
                query = query.where(
                    RunRecord.intent.in_([intent.value for intent in run_intents])
                )
            if graph_identities is not None:
                query = query.where(
                    or_(
                        *[
                            and_(
                                RunRecord.graph_name == name,
                                RunRecord.graph_version == version,
                            )
                            for name, version in graph_identities
                        ]
                    )
                )
            record = await session.scalar(
                query.order_by(
                    RunRecord.available_at,
                    RunRecord.created_at,
                    RunRecord.id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if record is None:
                return None
            record.dispatch_status = DispatchStatus.CLAIMED.value
            record.current_worker_id = worker_id
            record.current_worker_name = worker_name
            record.fencing_token += 1
            record.attempt += 1
            record.lease_acquired_at = now
            record.lease_expires_at = now + lease_duration
            record.heartbeat_at = now
            record.updated_at = now
            record.last_release_reason = None
            record.last_dispatch_error = None
            await _append_event(
                session,
                record,
                EventType.DISPATCH_CLAIMED,
                {
                    "worker_id": str(worker_id),
                    "worker_name": worker_name,
                    "fencing_token": record.fencing_token,
                    "attempt": record.attempt,
                    "lease_expires_at": record.lease_expires_at.isoformat(),
                },
            )
            return _lease(record)

    async def heartbeat(
        self,
        lease: RunLease,
        *,
        lease_duration: timedelta,
    ) -> RunLease:
        async with self._sessions.begin() as session:
            record, now = await _locked_live_lease(session, lease)
            record.heartbeat_at = now
            record.lease_expires_at = now + lease_duration
            return _lease(record)

    async def append_fenced_event(
        self,
        lease: RunLease,
        *,
        event_type: EventType,
        payload: dict[str, object],
        transition_id: str | None = None,
    ) -> RuntimeEvent:
        async with self._sessions.begin() as session:
            record, _ = await _locked_live_lease(session, lease)
            return await _append_event(
                session,
                record,
                event_type,
                payload,
                transition_id=transition_id,
            )

    async def release_to_queue(
        self,
        lease: RunLease,
        *,
        reason: str,
        max_attempts: int,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("Maximum attempts must be positive.")
        async with self._sessions.begin() as session:
            record, now = await _locked_live_lease(session, lease)
            _clear_lease(record)
            record.last_release_reason = reason
            if record.attempt >= max_attempts:
                await _mark_attempts_exhausted(session, record, now=now, reason=reason)
            else:
                record.dispatch_status = DispatchStatus.QUEUED.value
                record.available_at = now
                record.updated_at = now
                await _append_event(
                    session,
                    record,
                    EventType.DISPATCH_RELEASED,
                    {"reason": reason, "next_status": DispatchStatus.QUEUED.value},
                )

    async def request_cancellation(
        self,
        *,
        run_id: UUID,
        requested_by: str | None,
        reason: str | None,
    ) -> RuntimeEvent | None:
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            record = await session.scalar(
                select(RunRecord).where(RunRecord.id == run_id).with_for_update()
            )
            if record is None:
                raise KeyError(run_id)
            if record.status == RunStatus.CANCELLED.value:
                return None
            if record.dispatch_status == DispatchStatus.TERMINAL.value:
                raise DispatchConflict("Terminal Runs cannot be cancelled.")
            record.cancel_requested_at = record.cancel_requested_at or now
            record.cancel_requested_by = requested_by
            record.cancel_reason = reason
            record.updated_at = now
            if record.dispatch_status in {
                DispatchStatus.QUEUED.value,
                DispatchStatus.RETRY_SCHEDULED.value,
            }:
                return await _cancel_record(
                    session,
                    record,
                    now=now,
                    reason=reason,
                )
            if record.dispatch_status == DispatchStatus.WAITING.value:
                await _deny_pending_approvals_for_cancel(
                    session,
                    record,
                    now=now,
                    requested_by=requested_by,
                )
                return await _cancel_record(
                    session,
                    record,
                    now=now,
                    reason=reason,
                )
            if record.dispatch_status in {
                DispatchStatus.CLAIMED.value,
                DispatchStatus.EXECUTING.value,
            }:
                return await _append_event(
                    session,
                    record,
                    EventType.CANCELLATION_REQUESTED,
                    {
                        "requested_by": requested_by,
                        "reason": reason,
                        "dispatch_status": record.dispatch_status,
                    },
                    transition_id=f"cancel-requested:{record.id}",
                )
            raise DispatchConflict(
                f"Run in dispatch state {record.dispatch_status} cannot be cancelled."
            )

    async def is_cancel_requested(self, lease: RunLease) -> bool:
        async with self._sessions.begin() as session:
            record, _ = await _locked_live_lease(session, lease)
            return record.cancel_requested_at is not None

    async def mark_cancelled(
        self,
        lease: RunLease,
        *,
        reason: str,
    ) -> None:
        async with self._sessions.begin() as session:
            record, now = await _locked_live_lease(session, lease)
            await _cancel_record(session, record, now=now, reason=reason)

    async def release_for_approval_wait(
        self,
        lease: RunLease,
        *,
        approval_id: UUID,
        reason: str,
    ) -> None:
        async with self._sessions.begin() as session:
            record, now = await _locked_live_lease(session, lease)
            record.last_release_reason = reason
            record.last_dispatch_error = None
            _clear_lease(record)
            await _append_event(
                session,
                record,
                EventType.DISPATCH_RELEASED,
                {
                    "reason": reason,
                    "next_status": DispatchStatus.WAITING.value,
                    "approval_id": str(approval_id),
                },
            )
            await transition_run_status(
                session,
                record,
                status=RunStatus.PAUSED,
                dispatch_status=DispatchStatus.WAITING.value,
                now=now,
                reason=reason,
                extra_payload={
                    "approval_id": str(approval_id),
                },
            )

    async def requeue_after_approval(
        self,
        *,
        run_id: UUID,
        approval_id: UUID,
        reason: str,
    ) -> None:
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            approval = await session.get(
                ApprovalRecord,
                approval_id,
                with_for_update=True,
            )
            if approval is None or approval.run_id != run_id:
                raise KeyError(approval_id)
            record = await session.scalar(
                select(RunRecord).where(RunRecord.id == run_id).with_for_update()
            )
            if record is None:
                raise KeyError(run_id)
            if record.dispatch_status != DispatchStatus.WAITING.value:
                return
            record.available_at = now
            record.last_release_reason = reason
            record.last_dispatch_error = None
            await _append_event(
                session,
                record,
                EventType.DISPATCH_RELEASED,
                {
                    "reason": reason,
                    "next_status": DispatchStatus.QUEUED.value,
                    "approval_id": str(approval_id),
                },
            )
            await transition_run_status(
                session,
                record,
                status=RunStatus.RUNNING,
                dispatch_status=DispatchStatus.QUEUED.value,
                now=now,
                reason=reason,
                extra_payload={
                    "approval_id": str(approval_id),
                },
            )

    async def expire_pending_approvals(
        self,
        *,
        batch_size: int = 100,
    ) -> int:
        if batch_size < 1:
            raise ValueError("Batch size must be positive.")
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            approvals = list(
                await session.scalars(
                    select(ApprovalRecord)
                    .where(
                        ApprovalRecord.status == ApprovalStatus.PENDING.value,
                        ApprovalRecord.expires_at <= now,
                    )
                    .order_by(ApprovalRecord.expires_at, ApprovalRecord.id)
                    .with_for_update(skip_locked=True)
                    .limit(batch_size)
                )
            )
            for approval in approvals:
                approval.status = ApprovalStatus.EXPIRED.value
                approval.updated_at = now
                record = await session.scalar(
                    select(RunRecord)
                    .where(RunRecord.id == approval.run_id)
                    .with_for_update()
                )
                if record is None:
                    continue
                await _append_event(
                    session,
                    record,
                    EventType.APPROVAL_DECIDED,
                    {
                        "approval_id": str(approval.id),
                        "status": ApprovalStatus.EXPIRED.value,
                    },
                    transition_id=f"approval-expired:{approval.id}",
                )
                if record.dispatch_status == DispatchStatus.WAITING.value:
                    record.available_at = now
                    record.last_release_reason = "approval_expired"
                    record.last_dispatch_error = None
                    await _append_event(
                        session,
                        record,
                        EventType.DISPATCH_RELEASED,
                        {
                            "reason": "approval_expired",
                            "next_status": DispatchStatus.QUEUED.value,
                            "approval_id": str(approval.id),
                        },
                    )
                    await transition_run_status(
                        session,
                        record,
                        status=RunStatus.RUNNING,
                        dispatch_status=DispatchStatus.QUEUED.value,
                        now=now,
                        reason="approval_expired",
                        extra_payload={"approval_id": str(approval.id)},
                    )
            return len(approvals)

    async def release_for_retry(
        self,
        lease: RunLease,
        *,
        delay: timedelta,
        reason: str,
        max_attempts: int,
        error: str | None = None,
    ) -> None:
        if delay < timedelta(0):
            raise ValueError("Retry delay cannot be negative.")
        if max_attempts < 1:
            raise ValueError("Maximum attempts must be positive.")
        async with self._sessions.begin() as session:
            record, now = await _locked_live_lease(session, lease)
            _clear_lease(record)
            record.last_release_reason = reason
            record.last_dispatch_error = error
            if record.attempt >= max_attempts:
                await _mark_attempts_exhausted(session, record, now=now, reason=reason)
            else:
                record.dispatch_status = DispatchStatus.RETRY_SCHEDULED.value
                record.available_at = now + delay
                record.updated_at = now
                await _append_event(
                    session,
                    record,
                    EventType.DISPATCH_RETRY_SCHEDULED,
                    {
                        "reason": reason,
                        "available_at": record.available_at.isoformat(),
                        "error": error,
                    },
                )

    async def recover_expired(
        self,
        *,
        max_attempts: int,
        batch_size: int = 100,
    ) -> int:
        if max_attempts < 1:
            raise ValueError("Maximum attempts must be positive.")
        if batch_size < 1:
            raise ValueError("Batch size must be positive.")
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            records = list(
                await session.scalars(
                    select(RunRecord)
                    .where(
                        RunRecord.dispatch_status.in_(
                            [
                                DispatchStatus.CLAIMED.value,
                                DispatchStatus.EXECUTING.value,
                            ]
                        ),
                        RunRecord.lease_expires_at <= now,
                    )
                    .order_by(RunRecord.lease_expires_at, RunRecord.id)
                    .with_for_update(skip_locked=True)
                    .limit(batch_size)
                )
            )
            for record in records:
                expired_worker = record.current_worker_id
                expired_token = record.fencing_token
                _clear_lease(record)
                if record.attempt >= max_attempts:
                    record.last_release_reason = "maximum attempts exceeded"
                    await transition_run_status(
                        session,
                        record,
                        status=RunStatus.RECOVERY_REQUIRED,
                        dispatch_status=DispatchStatus.TERMINAL.value,
                        now=now,
                        reason="maximum attempts exceeded",
                    )
                    await _append_event(
                        session,
                        record,
                        EventType.DISPATCH_RECOVERY_REQUIRED,
                        {
                            "expired_worker_id": str(expired_worker),
                            "fencing_token": expired_token,
                            "attempt": record.attempt,
                        },
                    )
                else:
                    record.dispatch_status = DispatchStatus.QUEUED.value
                    record.available_at = now
                    record.updated_at = now
                    record.last_release_reason = "lease expired"
                    await _append_event(
                        session,
                        record,
                        EventType.DISPATCH_LEASE_EXPIRED,
                        {
                            "expired_worker_id": str(expired_worker),
                            "fencing_token": expired_token,
                            "attempt": record.attempt,
                        },
                    )
            return len(records)

    async def start_execution(
        self,
        lease: RunLease,
        *,
        graph_name: str,
        graph_version: int,
    ) -> None:
        async with self._sessions.begin() as session:
            record, now = await _locked_live_lease(session, lease)
            if record.graph_name != graph_name or record.graph_version != graph_version:
                raise ValueError("Run graph identity does not match the executor.")
            await transition_run_status(
                session,
                record,
                status=RunStatus.RUNNING,
                dispatch_status=DispatchStatus.EXECUTING.value,
                now=now,
                reason="graph_started",
            )
            await _append_event(
                session,
                record,
                EventType.GRAPH_STARTED,
                {
                    "graph_name": graph_name,
                    "graph_version": graph_version,
                    "fencing_token": lease.fencing_token,
                },
            )

    async def complete_execution(
        self,
        lease: RunLease,
        *,
        result_summary: str,
        recovered: bool = False,
        completion_kind: str = "runtime_probe",
        goal_executed: bool = False,
        result_text: str | None = None,
    ) -> None:
        async with self._sessions.begin() as session:
            record, now = await _locked_live_lease(session, lease)
            record.last_release_reason = "graph completed"
            record.result_text = result_text
            await transition_run_status(
                session,
                record,
                status=RunStatus.COMPLETED,
                dispatch_status=DispatchStatus.TERMINAL.value,
                now=now,
                reason="graph completed",
            )
            await transition_agents_for_run(
                session,
                run_id=record.id,
                status=AgentStatus.COMPLETED,
                now=now,
                reason="graph completed",
            )
            if completion_kind in {
                "read_only_coding",
                "modifying_unvalidated",
                "modifying_validated",
                "team_validated",
            }:
                await transition_todos_for_run(
                    session,
                    run_id=record.id,
                    status=TodoStatus.DONE,
                    now=now,
                    reason="graph completed",
                )
            _clear_lease(record)
            await _append_event(
                session,
                record,
                (EventType.GRAPH_RECOVERED if recovered else EventType.GRAPH_COMPLETED),
                {
                    "result_summary": result_summary,
                    "completion_kind": completion_kind,
                    "goal_executed": goal_executed,
                    "validation_complete": completion_kind != "modifying_unvalidated",
                },
            )

    async def fail_execution(
        self,
        lease: RunLease,
        *,
        reason: str,
    ) -> None:
        async with self._sessions.begin() as session:
            record, now = await _locked_live_lease(session, lease)
            record.last_release_reason = reason
            record.last_dispatch_error = reason
            await transition_run_status(
                session,
                record,
                status=RunStatus.FAILED,
                dispatch_status=DispatchStatus.TERMINAL.value,
                now=now,
                reason=reason,
            )
            await transition_agents_for_run(
                session,
                run_id=record.id,
                status=AgentStatus.FAILED,
                now=now,
                reason=reason,
            )
            await transition_todos_for_run(
                session,
                run_id=record.id,
                status=TodoStatus.BLOCKED,
                now=now,
                reason=reason,
                blocker=reason,
            )
            _clear_lease(record)

    async def mark_recovery_required(
        self,
        lease: RunLease,
        *,
        reason: str,
    ) -> None:
        async with self._sessions.begin() as session:
            record, now = await _locked_live_lease(session, lease)
            record.last_release_reason = reason
            record.last_dispatch_error = reason
            await transition_run_status(
                session,
                record,
                status=RunStatus.RECOVERY_REQUIRED,
                dispatch_status=DispatchStatus.TERMINAL.value,
                now=now,
                reason=reason,
            )
            await transition_agents_for_run(
                session,
                run_id=record.id,
                status=AgentStatus.FAILED,
                now=now,
                reason=reason,
            )
            _clear_lease(record)
            await _append_event(
                session,
                record,
                EventType.DISPATCH_RECOVERY_REQUIRED,
                {"reason": reason},
            )


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.clock_timestamp()))
    if value is None:
        raise RuntimeError("PostgreSQL did not return its current time.")
    return cast(datetime, value)


async def _locked_live_lease(
    session: AsyncSession,
    lease: RunLease,
) -> tuple[RunRecord, datetime]:
    now = await _database_now(session)
    record = await session.scalar(
        select(RunRecord).where(RunRecord.id == lease.run_id).with_for_update()
    )
    if (
        record is None
        or record.current_worker_id != lease.worker_id
        or record.fencing_token != lease.fencing_token
        or record.dispatch_status
        not in {
            DispatchStatus.CLAIMED.value,
            DispatchStatus.EXECUTING.value,
        }
        or record.lease_expires_at is None
        or record.lease_expires_at <= now
    ):
        raise LeaseLost(f"Lease is no longer valid for Run {lease.run_id}.")
    return record, now


async def _append_event(
    session: AsyncSession,
    record: RunRecord,
    event_type: EventType,
    payload: dict[str, object],
    *,
    transition_id: str | None = None,
) -> RuntimeEvent:
    if transition_id is not None:
        existing = await session.scalar(
            select(RuntimeEventRecord).where(
                RuntimeEventRecord.run_id == record.id,
                RuntimeEventRecord.transition_id == transition_id,
            )
        )
        if existing is not None:
            return _event_from_record(existing)
    current = await session.scalar(
        select(func.max(RuntimeEventRecord.sequence)).where(
            RuntimeEventRecord.run_id == record.id
        )
    )
    event = RuntimeEvent(
        run_id=record.id,
        sequence=(current or 0) + 1,
        transition_id=transition_id,
        event_type=event_type,
        payload=payload,
        trace_id=record.id.hex,
    )
    session.add(
        RuntimeEventRecord(
            id=event.id,
            run_id=event.run_id,
            sequence=event.sequence,
            transition_id=event.transition_id,
            event_type=event.event_type.value,
            payload=event.payload,
            team_id=None,
            agent_id=None,
            parent_agent_id=None,
            task_id=None,
            trace_id=event.trace_id,
            span_id=None,
            created_at=event.created_at,
        )
    )
    return event


def _event_from_record(record: RuntimeEventRecord) -> RuntimeEvent:
    return RuntimeEvent(
        id=record.id,
        run_id=record.run_id,
        sequence=record.sequence,
        transition_id=record.transition_id,
        event_type=EventType(record.event_type),
        payload={str(key): value for key, value in record.payload.items()},
        team_id=record.team_id,
        agent_id=record.agent_id,
        parent_agent_id=record.parent_agent_id,
        task_id=record.task_id,
        trace_id=record.trace_id,
        span_id=record.span_id,
        created_at=record.created_at,
    )


def _clear_lease(record: RunRecord) -> None:
    record.current_worker_id = None
    record.current_worker_name = None
    record.lease_acquired_at = None
    record.lease_expires_at = None
    record.heartbeat_at = None


async def _mark_attempts_exhausted(
    session: AsyncSession,
    record: RunRecord,
    *,
    now: datetime,
    reason: str,
) -> None:
    await transition_run_status(
        session,
        record,
        status=RunStatus.RECOVERY_REQUIRED,
        dispatch_status=DispatchStatus.TERMINAL.value,
        now=now,
        reason=reason,
    )
    await transition_agents_for_run(
        session,
        run_id=record.id,
        status=AgentStatus.FAILED,
        now=now,
        reason=reason,
    )
    await _append_event(
        session,
        record,
        EventType.DISPATCH_RECOVERY_REQUIRED,
        {
            "attempt": record.attempt,
            "reason": reason,
        },
    )


async def _cancel_record(
    session: AsyncSession,
    record: RunRecord,
    *,
    now: datetime,
    reason: str | None,
) -> RuntimeEvent:
    record.available_at = now
    record.last_release_reason = "cancelled"
    record.last_dispatch_error = None
    event = await transition_run_status(
        session,
        record,
        status=RunStatus.CANCELLED,
        dispatch_status=DispatchStatus.TERMINAL.value,
        now=now,
        reason=reason,
        transition_id=f"cancelled:{record.id}",
    )
    await transition_agents_for_run(
        session,
        run_id=record.id,
        status=AgentStatus.CANCELLED,
        now=now,
        reason=reason,
    )
    await transition_todos_for_run(
        session,
        run_id=record.id,
        status=TodoStatus.CANCELLED,
        now=now,
        reason=reason,
    )
    _clear_lease(record)
    return event


async def _deny_pending_approvals_for_cancel(
    session: AsyncSession,
    record: RunRecord,
    *,
    now: datetime,
    requested_by: str | None,
) -> None:
    approvals = list(
        await session.scalars(
            select(ApprovalRecord)
            .where(
                ApprovalRecord.run_id == record.id,
                ApprovalRecord.status == ApprovalStatus.PENDING.value,
            )
            .with_for_update(skip_locked=True)
        )
    )
    for approval in approvals:
        approval.status = ApprovalStatus.DENIED.value
        approval.decided_at = now
        approval.decided_by = requested_by
        approval.decision_reason = "run_cancelled"
        approval.updated_at = now
        await _append_event(
            session,
            record,
            EventType.APPROVAL_DECIDED,
            {
                "approval_id": str(approval.id),
                "status": ApprovalStatus.DENIED.value,
                "reason": "run_cancelled",
            },
            transition_id=f"approval-cancelled:{approval.id}",
        )


def _lease(record: RunRecord) -> RunLease:
    if (
        record.current_worker_id is None
        or record.current_worker_name is None
        or record.lease_acquired_at is None
        or record.lease_expires_at is None
        or record.heartbeat_at is None
    ):
        raise RuntimeError("Claimed Run is missing lease fields.")
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
