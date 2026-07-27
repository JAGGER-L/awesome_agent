from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from awesome_agent.core.workspace import WorkspaceIdentity, WorkspaceTrust
from awesome_agent.storage.application_sqlite import ApplicationSQLite


class SQLiteWorkspaceTrustStore:
    def __init__(self, database: ApplicationSQLite) -> None:
        self._database = database

    async def get(self, workspace_key: str) -> WorkspaceTrust | None:
        def read(connection: sqlite3.Connection) -> WorkspaceTrust | None:
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

        return await self._database.read(read)

    async def accept(self, identity: WorkspaceIdentity) -> WorkspaceTrust:
        record = WorkspaceTrust(
            workspace_key=identity.key,
            canonical_path=identity.canonical_path,
            trusted_at=datetime.now(UTC),
        )

        def write(connection: sqlite3.Connection) -> WorkspaceTrust:
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

        return await self._database.write(write)

    async def revoke(self, workspace_key: str) -> bool:
        def write(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                "DELETE FROM trusted_workspaces WHERE workspace_key = ?",
                (workspace_key,),
            )
            return cursor.rowcount > 0

        return await self._database.write(write)
