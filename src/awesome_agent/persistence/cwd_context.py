from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from awesome_agent.persistence.models import CwdContextSnapshotRecord
from awesome_agent.runtime.cwd_context import CwdContextSnapshot


class PostgresCwdContextSnapshotRepository:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self._sessions = sessions

    async def latest_for_thread(
        self,
        thread_id: UUID,
        working_directory: str,
    ) -> CwdContextSnapshot | None:
        async with self._sessions() as session:
            result = await session.execute(
                select(CwdContextSnapshotRecord)
                .where(CwdContextSnapshotRecord.thread_id == thread_id)
                .where(CwdContextSnapshotRecord.working_directory == working_directory)
                .order_by(CwdContextSnapshotRecord.created_at.desc())
                .limit(1)
            )
            record = result.scalar_one_or_none()
            if record is None:
                return None
            return CwdContextSnapshot.model_validate(record.payload)

    async def save(self, snapshot: CwdContextSnapshot) -> None:
        async with self._sessions() as session:
            existing = await session.get(CwdContextSnapshotRecord, snapshot.id)
            if existing is not None:
                return
            session.add(
                CwdContextSnapshotRecord(
                    snapshot_id=snapshot.id,
                    thread_id=snapshot.thread_id,
                    working_directory=snapshot.working_directory,
                    status=snapshot.status,
                    payload=snapshot.model_dump(mode="json"),
                    created_at=snapshot.created_at,
                )
            )
            await session.commit()
