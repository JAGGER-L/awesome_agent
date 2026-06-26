from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.artifacts.store import ArtifactMetadata
from awesome_agent.persistence.models import ArtifactRecord


class PostgresArtifactMetadataRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def record(self, metadata: ArtifactMetadata) -> ArtifactMetadata:
        async with self._sessions.begin() as session:
            existing = await session.get(ArtifactRecord, metadata.id)
            if existing is None:
                session.add(_to_record(metadata))
            else:
                existing.summary = metadata.summary
        return metadata

    async def get(self, artifact_id: UUID) -> ArtifactMetadata:
        async with self._sessions() as session:
            record = await session.get(ArtifactRecord, artifact_id)
        if record is None:
            raise KeyError(artifact_id)
        return _from_record(record)

    async def list_for_run(self, run_id: UUID) -> list[ArtifactMetadata]:
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(ArtifactRecord)
                    .where(ArtifactRecord.run_id == run_id)
                    .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
                )
            )
        return [_from_record(record) for record in records]


def _to_record(metadata: ArtifactMetadata) -> ArtifactRecord:
    return ArtifactRecord(
        id=metadata.id,
        run_id=metadata.run_id,
        agent_id=metadata.agent_id,
        artifact_type=metadata.artifact_type,
        path=str(metadata.path),
        sha256=metadata.sha256,
        size=metadata.size,
        mime_type=metadata.mime_type,
        summary=metadata.summary,
        created_at=metadata.created_at,
    )


def _from_record(record: ArtifactRecord) -> ArtifactMetadata:
    return ArtifactMetadata(
        id=record.id,
        run_id=record.run_id,
        agent_id=record.agent_id,
        artifact_type=record.artifact_type,
        path=Path(record.path),
        sha256=record.sha256,
        size=record.size,
        mime_type=record.mime_type,
        summary=record.summary,
        created_at=record.created_at,
    )
