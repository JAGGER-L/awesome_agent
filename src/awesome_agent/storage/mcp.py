from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from awesome_agent.extensions.mcp.models import mcp_config_hash
from awesome_agent.storage.application_sqlite import ApplicationSQLite

__all__ = ["SQLiteMcpEnablementStore", "mcp_config_hash"]


class SQLiteMcpEnablementStore:
    def __init__(self, database: ApplicationSQLite) -> None:
        self._database = database

    async def enable(
        self, workspace_key: str, server_id: str, config_hash: str
    ) -> None:
        enabled_at = datetime.now(UTC).isoformat()

        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                INSERT INTO mcp_enablements (
                    workspace_key, server_id, config_hash, enabled_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(workspace_key, server_id) DO UPDATE SET
                    config_hash = excluded.config_hash,
                    enabled_at = excluded.enabled_at
                """,
                (workspace_key, server_id, config_hash, enabled_at),
            )

        await self._database.write(write)

    async def disable(self, workspace_key: str, server_id: str) -> None:
        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                "DELETE FROM mcp_enablements WHERE workspace_key = ? AND server_id = ?",
                (workspace_key, server_id),
            )

        await self._database.write(write)

    async def snapshot(self, workspace_key: str) -> dict[str, str]:
        def read(connection: sqlite3.Connection) -> dict[str, str]:
            rows = connection.execute(
                "SELECT server_id, config_hash FROM mcp_enablements "
                "WHERE workspace_key = ?",
                (workspace_key,),
            ).fetchall()
            return {str(row["server_id"]): str(row["config_hash"]) for row in rows}

        return await self._database.read(read)
