from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import RuntimeEvent
from awesome_agent.persistence.models import RuntimeEventRecord


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: RuntimeEvent) -> None:
        self._session.add(
            RuntimeEventRecord(
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
        )
        await self._session.flush()

    async def list_for_run(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> list[RuntimeEvent]:
        result = await self._session.scalars(
            select(RuntimeEventRecord)
            .where(
                RuntimeEventRecord.run_id == run_id,
                RuntimeEventRecord.sequence > after_sequence,
            )
            .order_by(RuntimeEventRecord.sequence)
        )
        return [
            RuntimeEvent(
                id=row.id,
                run_id=row.run_id,
                sequence=row.sequence,
                event_type=EventType(row.event_type),
                payload=row.payload,
                team_id=row.team_id,
                agent_id=row.agent_id,
                parent_agent_id=row.parent_agent_id,
                task_id=row.task_id,
                trace_id=row.trace_id,
                span_id=row.span_id,
                created_at=row.created_at,
            )
            for row in result
        ]
