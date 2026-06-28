from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.persistence.models import WorkerHeartbeatRecord
from awesome_agent.runtime.worker_heartbeats import (
    GraphIdentity,
    WorkerHeartbeat,
    WorkerHeartbeatStatus,
)


class PostgresWorkerHeartbeatRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def upsert(self, heartbeat: WorkerHeartbeat) -> None:
        values = {
            "worker_id": heartbeat.worker_id,
            "worker_name": heartbeat.worker_name,
            "started_at": heartbeat.started_at,
            "heartbeat_at": heartbeat.heartbeat_at,
            "supported_graphs": [
                {"name": graph.name} for graph in heartbeat.supported_graphs
            ],
            "status": heartbeat.status.value,
        }
        statement = insert(WorkerHeartbeatRecord).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[WorkerHeartbeatRecord.worker_id],
            set_={
                "worker_name": statement.excluded.worker_name,
                "started_at": statement.excluded.started_at,
                "heartbeat_at": statement.excluded.heartbeat_at,
                "supported_graphs": statement.excluded.supported_graphs,
                "status": statement.excluded.status,
            },
        )
        async with self.sessions.begin() as session:
            await session.execute(statement)

    async def list_recent(self, *, stale_after: datetime) -> list[WorkerHeartbeat]:
        statement = (
            select(WorkerHeartbeatRecord)
            .where(WorkerHeartbeatRecord.heartbeat_at >= stale_after)
            .order_by(WorkerHeartbeatRecord.heartbeat_at.desc())
        )
        async with self.sessions() as session:
            records = (await session.scalars(statement)).all()
        return [_to_heartbeat(record) for record in records]

    async def mark_stopping(self, worker_id: UUID) -> None:
        statement = (
            update(WorkerHeartbeatRecord)
            .where(WorkerHeartbeatRecord.worker_id == worker_id)
            .values(status=WorkerHeartbeatStatus.STOPPING.value)
        )
        async with self.sessions.begin() as session:
            await session.execute(statement)


def _to_heartbeat(record: WorkerHeartbeatRecord) -> WorkerHeartbeat:
    return WorkerHeartbeat(
        worker_id=record.worker_id,
        worker_name=record.worker_name,
        started_at=record.started_at,
        heartbeat_at=record.heartbeat_at,
        supported_graphs=[
            GraphIdentity(str(graph["name"])) for graph in record.supported_graphs
        ],
        status=WorkerHeartbeatStatus(record.status),
    )
