from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ArtifactMetadata(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    agent_id: UUID | None = None
    artifact_type: str
    path: Path
    sha256: str
    size: int
    mime_type: str
    summary: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._metadata: dict[UUID, ArtifactMetadata] = {}

    def write(
        self,
        *,
        run_id: UUID,
        artifact_type: str,
        filename: str,
        content: bytes,
        mime_type: str,
        summary: str = "",
        agent_id: UUID | None = None,
    ) -> ArtifactMetadata:
        safe_name = Path(filename).name
        artifact_id = uuid4()
        directory = self._root / str(run_id) / artifact_type
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{artifact_id}-{safe_name}"
        path.write_bytes(content)
        metadata = ArtifactMetadata(
            id=artifact_id,
            run_id=run_id,
            agent_id=agent_id,
            artifact_type=artifact_type,
            path=path,
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
            mime_type=mime_type,
            summary=summary,
        )
        self._metadata[metadata.id] = metadata
        return metadata

    def get(self, artifact_id: UUID) -> ArtifactMetadata:
        return self._metadata[artifact_id]

    def list_for_run(self, run_id: UUID) -> list[ArtifactMetadata]:
        return [
            metadata
            for metadata in self._metadata.values()
            if metadata.run_id == run_id
        ]

    def delete_run(self, run_id: UUID) -> None:
        for metadata in list(self.list_for_run(run_id)):
            metadata.path.unlink(missing_ok=True)
            self._metadata.pop(metadata.id, None)
        run_root = self._root / str(run_id)
        for directory in sorted(run_root.glob("**/*"), reverse=True):
            if directory.is_dir():
                directory.rmdir()
        if run_root.exists():
            run_root.rmdir()
