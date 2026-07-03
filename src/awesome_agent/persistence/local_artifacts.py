from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from awesome_agent.artifacts.repository import ArtifactMetadataRepository
from awesome_agent.artifacts.store import ArtifactMetadata

_SCHEMA_VERSION = "1"


class LocalArtifactMetadataRepository(ArtifactMetadataRepository):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        self._connection.close()

    async def record(self, metadata: ArtifactMetadata) -> ArtifactMetadata:
        with self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO artifacts
                  (id, run_id, path, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(metadata.id),
                    str(metadata.run_id),
                    str(metadata.path),
                    metadata.model_dump_json(),
                    metadata.created_at.isoformat(),
                ),
            )
        return metadata

    async def get(self, artifact_id: UUID) -> ArtifactMetadata:
        row = self._connection.execute(
            """
            SELECT payload_json FROM artifacts
            WHERE id = ?
            """,
            (str(artifact_id),),
        ).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return ArtifactMetadata.model_validate_json(row["payload_json"])

    async def list_for_run(self, run_id: UUID) -> list[ArtifactMetadata]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM artifacts
            WHERE run_id = ?
            ORDER BY created_at ASC, path ASC, id ASC
            """,
            (str(run_id),),
        ).fetchall()
        return [
            ArtifactMetadata.model_validate_json(row["payload_json"]) for row in rows
        ]

    def _ensure_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS artifact_metadata (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                )
                """
            )
            existing_version = self._connection.execute(
                """
                SELECT value FROM artifact_metadata
                WHERE key = 'schema_version'
                """
            ).fetchone()
            if (
                existing_version is not None
                and existing_version["value"] != _SCHEMA_VERSION
            ):
                raise RuntimeError(
                    "Local artifact database is from a newer awesome_agent version."
                )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                  id TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  path TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO artifact_metadata (key, value)
                VALUES ('schema_version', ?)
                """,
                (_SCHEMA_VERSION,),
            )
