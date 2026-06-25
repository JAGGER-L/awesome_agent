from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.domain.enums import (
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunStatus,
)
from awesome_agent.domain.models import RunLease, RuntimeEvent
from awesome_agent.persistence.models import RunRecord, RuntimeEventRecord, TodoRecord
from awesome_agent.runtime.dispatch import LeaseLost, RunDispatcher


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
                await _mark_attempts_exhausted(session, record, reason)
            else:
                record.dispatch_status = DispatchStatus.QUEUED.value
                record.available_at = now
                await _append_event(
                    session,
                    record,
                    EventType.DISPATCH_RELEASED,
                    {"reason": reason, "next_status": DispatchStatus.QUEUED.value},
                )

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
                await _mark_attempts_exhausted(session, record, reason)
            else:
                record.dispatch_status = DispatchStatus.RETRY_SCHEDULED.value
                record.available_at = now + delay
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
                    record.status = RunStatus.RECOVERY_REQUIRED.value
                    record.dispatch_status = DispatchStatus.TERMINAL.value
                    record.last_release_reason = "maximum attempts exceeded"
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
            record, _ = await _locked_live_lease(session, lease)
            if record.graph_name != graph_name or record.graph_version != graph_version:
                raise ValueError("Run graph identity does not match the executor.")
            record.status = RunStatus.RUNNING.value
            record.dispatch_status = DispatchStatus.EXECUTING.value
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
            record, _ = await _locked_live_lease(session, lease)
            record.status = RunStatus.COMPLETED.value
            record.dispatch_status = DispatchStatus.TERMINAL.value
            record.last_release_reason = "graph completed"
            record.result_text = result_text
            if completion_kind == "read_only_coding":
                for todo in await session.scalars(
                    select(TodoRecord).where(TodoRecord.run_id == record.id)
                ):
                    todo.status = "done"
                    todo.updated_at = record.updated_at
            _clear_lease(record)
            await _append_event(
                session,
                record,
                (EventType.GRAPH_RECOVERED if recovered else EventType.GRAPH_COMPLETED),
                {
                    "result_summary": result_summary,
                    "completion_kind": completion_kind,
                    "goal_executed": goal_executed,
                },
            )

    async def fail_execution(
        self,
        lease: RunLease,
        *,
        reason: str,
    ) -> None:
        async with self._sessions.begin() as session:
            record, _ = await _locked_live_lease(session, lease)
            record.status = RunStatus.FAILED.value
            record.dispatch_status = DispatchStatus.TERMINAL.value
            record.last_release_reason = reason
            record.last_dispatch_error = reason
            for todo in await session.scalars(
                select(TodoRecord).where(TodoRecord.run_id == record.id)
            ):
                todo.status = "blocked"
                todo.blocker = reason
            _clear_lease(record)
            await _append_event(
                session,
                record,
                EventType.RUN_STATUS_CHANGED,
                {
                    "status": RunStatus.FAILED.value,
                    "dispatch_status": DispatchStatus.TERMINAL.value,
                    "reason": reason,
                },
            )

    async def mark_recovery_required(
        self,
        lease: RunLease,
        *,
        reason: str,
    ) -> None:
        async with self._sessions.begin() as session:
            record, _ = await _locked_live_lease(session, lease)
            record.status = RunStatus.RECOVERY_REQUIRED.value
            record.dispatch_status = DispatchStatus.TERMINAL.value
            record.last_release_reason = reason
            record.last_dispatch_error = reason
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
            trace_id=None,
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
    reason: str,
) -> None:
    record.status = RunStatus.RECOVERY_REQUIRED.value
    record.dispatch_status = DispatchStatus.TERMINAL.value
    await _append_event(
        session,
        record,
        EventType.DISPATCH_RECOVERY_REQUIRED,
        {
            "attempt": record.attempt,
            "reason": reason,
        },
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
