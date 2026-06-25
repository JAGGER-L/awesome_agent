from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    DispatchStatus,
    EventType,
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
            record.dispatch_status = run.dispatch_status.value
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
        dispatch_status=DispatchStatus(record.dispatch_status),
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
        dispatch_status=run.dispatch_status.value,
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
        created_at=agent.created_at,
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
        created_at=record.created_at,
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


def _event_to_record(event: RuntimeEvent) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        id=event.id,
        run_id=event.run_id,
        sequence=event.sequence,
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
