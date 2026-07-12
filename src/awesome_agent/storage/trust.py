from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from awesome_agent.core.workspace import WorkspaceIdentity, WorkspaceTrust
from awesome_agent.storage.database import application_connection


class SQLiteWorkspaceTrustStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, workspace_key: str) -> WorkspaceTrust | None:
        with application_connection(self._path) as connection:
            row = connection.execute(
                "SELECT workspace_key, canonical_path, trusted_at "
                "FROM trusted_workspaces WHERE workspace_key = ?",
                (workspace_key,),
            ).fetchone()
        if row is None:
            return None
        return WorkspaceTrust(
            workspace_key=row["workspace_key"],
            canonical_path=Path(row["canonical_path"]),
            trusted_at=datetime.fromisoformat(row["trusted_at"]),
        )

    def accept(self, identity: WorkspaceIdentity) -> WorkspaceTrust:
        record = WorkspaceTrust(
            workspace_key=identity.key,
            canonical_path=identity.canonical_path,
            trusted_at=datetime.now(UTC),
        )
        with application_connection(self._path) as connection, connection:
            connection.execute(
                "INSERT INTO trusted_workspaces "
                "(workspace_key, canonical_path, trusted_at) VALUES (?, ?, ?) "
                "ON CONFLICT(workspace_key) DO UPDATE SET "
                "canonical_path = excluded.canonical_path, "
                "trusted_at = excluded.trusted_at",
                (
                    record.workspace_key,
                    str(record.canonical_path),
                    record.trusted_at.isoformat(),
                ),
            )
        return record

    def revoke(self, workspace_key: str) -> bool:
        with application_connection(self._path) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM trusted_workspaces WHERE workspace_key = ?",
                (workspace_key,),
            )
        return cursor.rowcount > 0
