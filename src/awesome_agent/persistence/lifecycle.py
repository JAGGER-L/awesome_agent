from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from awesome_agent.domain.enums import AgentStatus, EventType, RunStatus, TodoStatus
from awesome_agent.domain.models import RuntimeEvent
from awesome_agent.persistence.models import (
    AgentRecord,
    RunRecord,
    RuntimeEventRecord,
    TodoRecord,
)


async def append_lifecycle_event(
    session: AsyncSession,
    *,
    run_id: UUID,
    event_type: EventType,
    payload: dict[str, object],
    created_at: datetime,
    transition_id: str | None = None,
    agent_id: UUID | None = None,
    task_id: UUID | None = None,
) -> RuntimeEvent:
    if transition_id is not None:
        existing = await session.scalar(
            select(RuntimeEventRecord).where(
                RuntimeEventRecord.run_id == run_id,
                RuntimeEventRecord.transition_id == transition_id,
            )
        )
        if existing is not None:
            return _event_from_record(existing)
    current = await session.scalar(
        select(func.max(RuntimeEventRecord.sequence)).where(
            RuntimeEventRecord.run_id == run_id
        )
    )
    event = RuntimeEvent(
        run_id=run_id,
        sequence=(current or 0) + 1,
        transition_id=transition_id,
        event_type=event_type,
        payload=payload,
        agent_id=agent_id,
        task_id=task_id,
        trace_id=run_id.hex,
        created_at=created_at,
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
            agent_id=event.agent_id,
            parent_agent_id=None,
            task_id=event.task_id,
            trace_id=event.trace_id,
            span_id=None,
            created_at=event.created_at,
        )
    )
    return event


async def transition_run_status(
    session: AsyncSession,
    record: RunRecord,
    *,
    status: RunStatus,
    dispatch_status: str,
    now: datetime,
    reason: str | None,
    transition_id: str | None = None,
    extra_payload: dict[str, object] | None = None,
) -> RuntimeEvent:
    previous_status = record.status
    previous_dispatch_status = record.dispatch_status
    record.status = status.value
    record.dispatch_status = dispatch_status
    record.updated_at = now
    payload: dict[str, object] = {
        "previous_status": previous_status,
        "status": record.status,
        "previous_dispatch_status": previous_dispatch_status,
        "dispatch_status": record.dispatch_status,
        "reason": reason,
        "updated_at": now.isoformat(),
    }
    if extra_payload:
        payload.update(extra_payload)
    return await append_lifecycle_event(
        session,
        run_id=record.id,
        event_type=EventType.RUN_STATUS_CHANGED,
        payload=payload,
        created_at=now,
        transition_id=transition_id,
    )


async def transition_agents_for_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    status: AgentStatus,
    now: datetime,
    reason: str | None,
) -> list[RuntimeEvent]:
    agents = list(
        await session.scalars(select(AgentRecord).where(AgentRecord.run_id == run_id))
    )
    events: list[RuntimeEvent] = []
    for agent in agents:
        if agent.status == status.value:
            continue
        previous_status = agent.status
        agent.status = status.value
        agent.revision += 1
        agent.updated_at = now
        events.append(
            await append_lifecycle_event(
                session,
                run_id=run_id,
                event_type=EventType.AGENT_STATUS_CHANGED,
                payload={
                    "agent_id": str(agent.id),
                    "previous_status": previous_status,
                    "status": agent.status,
                    "reason": reason,
                    "revision": agent.revision,
                    "updated_at": now.isoformat(),
                },
                created_at=now,
                agent_id=agent.id,
            )
        )
    return events


async def transition_todos_for_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    status: TodoStatus,
    now: datetime,
    reason: str | None,
    blocker: str | None = None,
) -> list[RuntimeEvent]:
    todos = list(
        await session.scalars(select(TodoRecord).where(TodoRecord.run_id == run_id))
    )
    events: list[RuntimeEvent] = []
    for todo in todos:
        if todo.status == status.value and todo.blocker == blocker:
            continue
        previous_status = todo.status
        todo.status = status.value
        todo.blocker = blocker
        todo.revision += 1
        todo.updated_at = now
        payload: dict[str, object] = {
            "todo_id": str(todo.id),
            "previous_status": previous_status,
            "status": todo.status,
            "reason": reason,
            "revision": todo.revision,
            "updated_at": now.isoformat(),
        }
        if blocker is not None:
            payload["blocker"] = blocker
        events.append(
            await append_lifecycle_event(
                session,
                run_id=run_id,
                event_type=EventType.TODO_STATUS_CHANGED,
                payload=payload,
                created_at=now,
                task_id=todo.id,
            )
        )
    return events


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
