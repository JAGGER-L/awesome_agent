from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    EventType,
    RunMode,
    RunStatus,
    TodoStatus,
)
from awesome_agent.domain.models import Agent, Run, RuntimeEvent, TodoItem
from awesome_agent.persistence.models import (
    AgentRecord,
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
            session.add(
                RunRecord(
                    id=run.id,
                    goal=run.goal,
                    mode=run.mode.value,
                    status=run.status.value,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                )
            )
            await session.flush()
            session.add(
                AgentRecord(
                    id=leader.id,
                    run_id=leader.run_id,
                    parent_agent_id=leader.parent_agent_id,
                    kind=leader.kind.value,
                    profile=leader.profile,
                    model=leader.model,
                    status=leader.status.value,
                    created_at=leader.created_at,
                )
            )

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
        created_at=record.created_at,
        updated_at=record.updated_at,
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
