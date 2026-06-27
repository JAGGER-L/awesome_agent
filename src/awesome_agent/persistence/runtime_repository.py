from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    DispatchStatus,
    EventType,
    ExecutionKind,
    IntakeReservationStatus,
    RunIntent,
    RunMode,
    RunStatus,
    TodoStatus,
    WorkspaceState,
)
from awesome_agent.domain.models import Agent, Run, RuntimeEvent, TodoItem
from awesome_agent.persistence.models import (
    AgentRecord,
    IntakeReservationRecord,
    RunRecord,
    RuntimeEventRecord,
    TodoRecord,
)
from awesome_agent.runtime.dispatch import DispatchConflict
from awesome_agent.runtime.repository import RuntimeRepository


class PostgresRuntimeRepository(RuntimeRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create_run(self, run: Run, leader: Agent) -> None:
        async with self._sessions.begin() as session:
            session.add(_run_to_record(run))
            await session.flush()
            session.add(_agent_to_record(leader))

    async def publish_intake(
        self,
        *,
        run: Run,
        leader: Agent,
        todo: TodoItem | None,
        events: list[RuntimeEvent],
        reservation_id: UUID,
    ) -> None:
        async with self._sessions.begin() as session:
            reservation = await session.get(
                IntakeReservationRecord,
                reservation_id,
            )
            if reservation is None:
                raise KeyError(reservation_id)
            if reservation.run_id != run.id:
                raise ValueError("Reservation does not belong to this Run.")
            session.add(_run_to_record(run))
            await session.flush()
            session.add(_agent_to_record(leader))
            if todo is not None:
                session.add(_todo_to_record(todo))
            session.add_all([_event_to_record(event) for event in events])
            reservation.status = IntakeReservationStatus.PUBLISHED.value
            reservation.updated_at = run.updated_at

    async def get_run(self, run_id: UUID) -> Run:
        async with self._sessions() as session:
            record = await session.get(RunRecord, run_id)
        if record is None:
            raise KeyError(run_id)
        return _run_from_record(record)

    async def update_run(self, run: Run) -> None:
        async with self._sessions.begin() as session:
            record = await session.get(RunRecord, run.id)
            if record is None:
                raise KeyError(run.id)
            record.goal = run.goal
            record.mode = run.mode.value
            record.status = run.status.value
            record.repository_id = run.repository_id
            record.base_commit = run.base_commit
            record.intent = run.intent.value
            record.execution_kind = run.execution_kind.value
            record.graph_name = run.graph_name
            record.graph_version = run.graph_version
            record.dispatch_status = run.dispatch_status.value
            record.available_at = run.available_at
            record.current_worker_id = run.current_worker_id
            record.current_worker_name = run.current_worker_name
            record.fencing_token = run.fencing_token
            record.attempt = run.attempt
            record.lease_acquired_at = run.lease_acquired_at
            record.lease_expires_at = run.lease_expires_at
            record.heartbeat_at = run.heartbeat_at
            record.last_release_reason = run.last_release_reason
            record.last_dispatch_error = run.last_dispatch_error
            record.cancel_requested_at = run.cancel_requested_at
            record.cancel_requested_by = run.cancel_requested_by
            record.cancel_reason = run.cancel_reason
            record.result_text = run.result_text
            record.workspace_path = (
                str(run.workspace_path) if run.workspace_path is not None else None
            )
            record.integration_branch = run.integration_branch
            record.workspace_state = (
                run.workspace_state.value if run.workspace_state is not None else None
            )
            record.graph_thread_id = run.graph_thread_id
            record.legacy = run.legacy
            record.updated_at = run.updated_at

    async def cancel_run(self, run_id: UUID) -> tuple[Run, RuntimeEvent | None]:
        async with self._sessions.begin() as session:
            record = await session.scalar(
                select(RunRecord).where(RunRecord.id == run_id).with_for_update()
            )
            if record is None:
                raise KeyError(run_id)
            if record.dispatch_status in {
                DispatchStatus.CLAIMED.value,
                DispatchStatus.EXECUTING.value,
            }:
                raise DispatchConflict(
                    "Claimed or executing Runs cannot be cancelled yet."
                )
            if record.status == RunStatus.CANCELLED.value:
                return _run_from_record(record), None
            record.status = RunStatus.CANCELLED.value
            record.dispatch_status = DispatchStatus.TERMINAL.value
            record.updated_at = cast(
                datetime,
                await session.scalar(select(func.clock_timestamp())),
            )
            event = await _append_locked_event(
                session,
                run_id,
                EventType.RUN_STATUS_CHANGED,
                {
                    "status": RunStatus.CANCELLED.value,
                    "dispatch_status": DispatchStatus.TERMINAL.value,
                },
            )
            return _run_from_record(record), event

    async def list_agents(self, run_id: UUID) -> list[Agent]:
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(AgentRecord)
                    .where(AgentRecord.run_id == run_id)
                    .order_by(AgentRecord.created_at, AgentRecord.id)
                )
            )
        return [_agent_from_record(record) for record in records]

    async def add_agent(self, agent: Agent) -> None:
        async with self._sessions.begin() as session:
            existing = await session.get(AgentRecord, agent.id)
            if existing is None:
                session.add(_agent_to_record(agent))

    async def list_todos(self, run_id: UUID) -> list[TodoItem]:
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(TodoRecord)
                    .where(TodoRecord.run_id == run_id)
                    .order_by(TodoRecord.created_at, TodoRecord.id)
                )
            )
        return [_todo_from_record(record) for record in records]

    async def add_todo(self, todo: TodoItem) -> None:
        async with self._sessions.begin() as session:
            existing = await session.get(TodoRecord, todo.id)
            if existing is None:
                session.add(_todo_to_record(todo))

    async def update_todo(self, todo: TodoItem) -> None:
        async with self._sessions.begin() as session:
            record = await session.get(TodoRecord, todo.id)
            if record is None:
                raise KeyError(todo.id)
            record.parent_id = todo.parent_id
            record.title = todo.title
            record.description = todo.description
            record.status = todo.status.value
            record.primary_owner_id = todo.primary_owner_id
            record.collaborator_ids = [str(value) for value in todo.collaborator_ids]
            record.acceptance_criteria = todo.acceptance_criteria
            record.blocker = todo.blocker
            record.revision = todo.revision
            record.updated_at = todo.updated_at

    async def append_event(
        self,
        *,
        run_id: UUID,
        event_type: EventType,
        payload: dict[str, object],
        agent_id: UUID | None = None,
    ) -> RuntimeEvent:
        async with self._sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:run_id))"),
                {"run_id": str(run_id)},
            )
            current = await session.scalar(
                select(func.max(RuntimeEventRecord.sequence)).where(
                    RuntimeEventRecord.run_id == run_id
                )
            )
            event = RuntimeEvent(
                run_id=run_id,
                sequence=(current or 0) + 1,
                event_type=event_type,
                payload=payload,
                agent_id=agent_id,
                trace_id=run_id.hex,
            )
            session.add(_event_to_record(event))
        return event

    async def list_events(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> list[RuntimeEvent]:
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(RuntimeEventRecord)
                    .where(
                        RuntimeEventRecord.run_id == run_id,
                        RuntimeEventRecord.sequence > after_sequence,
                    )
                    .order_by(RuntimeEventRecord.sequence)
                )
            )
        return [_event_from_record(record) for record in records]


def _run_from_record(record: RunRecord) -> Run:
    return Run(
        id=record.id,
        goal=record.goal,
        mode=RunMode(record.mode),
        status=RunStatus(record.status),
        repository_id=record.repository_id,
        base_commit=record.base_commit,
        intent=RunIntent(record.intent),
        execution_kind=ExecutionKind(record.execution_kind),
        graph_name=record.graph_name,
        graph_version=record.graph_version,
        dispatch_status=DispatchStatus(record.dispatch_status),
        available_at=record.available_at,
        current_worker_id=record.current_worker_id,
        current_worker_name=record.current_worker_name,
        fencing_token=record.fencing_token,
        attempt=record.attempt,
        lease_acquired_at=record.lease_acquired_at,
        lease_expires_at=record.lease_expires_at,
        heartbeat_at=record.heartbeat_at,
        last_release_reason=record.last_release_reason,
        last_dispatch_error=record.last_dispatch_error,
        cancel_requested_at=record.cancel_requested_at,
        cancel_requested_by=record.cancel_requested_by,
        cancel_reason=record.cancel_reason,
        result_text=record.result_text,
        workspace_path=(
            Path(record.workspace_path) if record.workspace_path is not None else None
        ),
        integration_branch=record.integration_branch,
        workspace_state=(
            WorkspaceState(record.workspace_state)
            if record.workspace_state is not None
            else None
        ),
        graph_thread_id=record.graph_thread_id,
        legacy=record.legacy,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _run_to_record(run: Run) -> RunRecord:
    return RunRecord(
        id=run.id,
        goal=run.goal,
        mode=run.mode.value,
        status=run.status.value,
        repository_id=run.repository_id,
        base_commit=run.base_commit,
        intent=run.intent.value,
        execution_kind=run.execution_kind.value,
        graph_name=run.graph_name,
        graph_version=run.graph_version,
        dispatch_status=run.dispatch_status.value,
        available_at=run.available_at,
        current_worker_id=run.current_worker_id,
        current_worker_name=run.current_worker_name,
        fencing_token=run.fencing_token,
        attempt=run.attempt,
        lease_acquired_at=run.lease_acquired_at,
        lease_expires_at=run.lease_expires_at,
        heartbeat_at=run.heartbeat_at,
        last_release_reason=run.last_release_reason,
        last_dispatch_error=run.last_dispatch_error,
        cancel_requested_at=run.cancel_requested_at,
        cancel_requested_by=run.cancel_requested_by,
        cancel_reason=run.cancel_reason,
        result_text=run.result_text,
        workspace_path=(
            str(run.workspace_path) if run.workspace_path is not None else None
        ),
        integration_branch=run.integration_branch,
        workspace_state=(
            run.workspace_state.value if run.workspace_state is not None else None
        ),
        graph_thread_id=run.graph_thread_id,
        legacy=run.legacy,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _agent_to_record(agent: Agent) -> AgentRecord:
    return AgentRecord(
        id=agent.id,
        run_id=agent.run_id,
        parent_agent_id=agent.parent_agent_id,
        kind=agent.kind.value,
        profile=agent.profile,
        model=agent.model,
        status=agent.status.value,
        revision=agent.revision,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _agent_from_record(record: AgentRecord) -> Agent:
    return Agent(
        id=record.id,
        run_id=record.run_id,
        parent_agent_id=record.parent_agent_id,
        kind=AgentKind(record.kind),
        profile=record.profile,
        model=record.model,
        status=AgentStatus(record.status),
        revision=record.revision,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _todo_from_record(record: TodoRecord) -> TodoItem:
    return TodoItem(
        id=record.id,
        run_id=record.run_id,
        parent_id=record.parent_id,
        title=record.title,
        description=record.description,
        status=TodoStatus(record.status),
        primary_owner_id=record.primary_owner_id,
        collaborator_ids=[UUID(value) for value in record.collaborator_ids],
        acceptance_criteria=record.acceptance_criteria,
        blocker=record.blocker,
        revision=record.revision,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _todo_to_record(todo: TodoItem) -> TodoRecord:
    return TodoRecord(
        id=todo.id,
        run_id=todo.run_id,
        parent_id=todo.parent_id,
        title=todo.title,
        description=todo.description,
        status=todo.status.value,
        primary_owner_id=todo.primary_owner_id,
        collaborator_ids=[str(value) for value in todo.collaborator_ids],
        acceptance_criteria=todo.acceptance_criteria,
        blocker=todo.blocker,
        revision=todo.revision,
        created_at=todo.created_at,
        updated_at=todo.updated_at,
    )


def _event_to_record(event: RuntimeEvent) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        id=event.id,
        run_id=event.run_id,
        sequence=event.sequence,
        transition_id=event.transition_id,
        event_type=event.event_type.value,
        payload=event.payload,
        team_id=event.team_id,
        agent_id=event.agent_id,
        parent_agent_id=event.parent_agent_id,
        task_id=event.task_id,
        trace_id=event.trace_id,
        span_id=event.span_id,
        created_at=event.created_at,
    )


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


async def _append_locked_event(
    session: AsyncSession,
    run_id: UUID,
    event_type: EventType,
    payload: dict[str, object],
) -> RuntimeEvent:
    current = await session.scalar(
        select(func.max(RuntimeEventRecord.sequence)).where(
            RuntimeEventRecord.run_id == run_id
        )
    )
    event = RuntimeEvent(
        run_id=run_id,
        sequence=(current or 0) + 1,
        event_type=event_type,
        payload=payload,
        trace_id=run_id.hex,
    )
    session.add(_event_to_record(event))
    return event
