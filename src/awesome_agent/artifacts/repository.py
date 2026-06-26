from __future__ import annotations

from typing import Protocol
from uuid import UUID

from awesome_agent.artifacts.store import ArtifactMetadata


class ArtifactMetadataRepository(Protocol):
    async def record(self, metadata: ArtifactMetadata) -> ArtifactMetadata:
        """Persist artifact metadata."""
        ...

    async def get(self, artifact_id: UUID) -> ArtifactMetadata:
        """Load one artifact metadata record."""
        ...

    async def list_for_run(self, run_id: UUID) -> list[ArtifactMetadata]:
        """Load artifact metadata records for a Run."""
        ...


class InMemoryArtifactMetadataRepository(ArtifactMetadataRepository):
    def __init__(self) -> None:
        self._metadata: dict[UUID, ArtifactMetadata] = {}

    async def record(self, metadata: ArtifactMetadata) -> ArtifactMetadata:
        self._metadata[metadata.id] = metadata
        return metadata

    async def get(self, artifact_id: UUID) -> ArtifactMetadata:
        return self._metadata[artifact_id]

    async def list_for_run(self, run_id: UUID) -> list[ArtifactMetadata]:
        return [
            metadata
            for metadata in self._metadata.values()
            if metadata.run_id == run_id
        ]
