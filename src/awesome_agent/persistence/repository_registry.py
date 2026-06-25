from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.domain.models import Repository
from awesome_agent.persistence.models import RepositoryRecord
from awesome_agent.repositories.registry import RepositoryRegistry


class PostgresRepositoryRegistry(RepositoryRegistry):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def upsert(self, repository: Repository) -> Repository:
        async with self._sessions.begin() as session:
            record = await session.scalar(
                select(RepositoryRecord).where(
                    RepositoryRecord.git_common_dir == str(repository.git_common_dir)
                )
            )
            if record is None:
                record = _to_record(repository)
                session.add(record)
            else:
                record.root = str(repository.root)
                record.display_name = repository.display_name
                record.default_branch = repository.default_branch
                record.enabled = repository.enabled
                record.updated_at = repository.updated_at
                record.last_seen_at = repository.last_seen_at
            await session.flush()
            return _from_record(record)

    async def get(self, repository_id: UUID) -> Repository:
        async with self._sessions() as session:
            record = await session.get(RepositoryRecord, repository_id)
        if record is None:
            raise KeyError(repository_id)
        return _from_record(record)

    async def list(self, *, enabled_only: bool = False) -> list[Repository]:
        query = select(RepositoryRecord)
        if enabled_only:
            query = query.where(RepositoryRecord.enabled.is_(True))
        query = query.order_by(RepositoryRecord.display_name, RepositoryRecord.id)
        async with self._sessions() as session:
            records = list(await session.scalars(query))
        return [_from_record(record) for record in records]

    async def disable(self, repository_id: UUID) -> Repository:
        async with self._sessions.begin() as session:
            record = await session.get(RepositoryRecord, repository_id)
            if record is None:
                raise KeyError(repository_id)
            record.enabled = False
            await session.flush()
            return _from_record(record)


def _to_record(repository: Repository) -> RepositoryRecord:
    return RepositoryRecord(
        id=repository.id,
        root=str(repository.root),
        display_name=repository.display_name,
        git_common_dir=str(repository.git_common_dir),
        default_branch=repository.default_branch,
        enabled=repository.enabled,
        created_at=repository.created_at,
        updated_at=repository.updated_at,
        last_seen_at=repository.last_seen_at,
    )


def _from_record(record: RepositoryRecord) -> Repository:
    return Repository(
        id=record.id,
        root=Path(record.root),
        display_name=record.display_name,
        git_common_dir=Path(record.git_common_dir),
        default_branch=record.default_branch,
        enabled=record.enabled,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_seen_at=record.last_seen_at,
    )
