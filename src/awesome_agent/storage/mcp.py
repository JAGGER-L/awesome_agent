from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from awesome_agent.extensions.mcp.models import mcp_config_hash
from awesome_agent.storage.database import application_connection

__all__ = ["SQLiteMcpEnablementStore", "mcp_config_hash"]


class SQLiteMcpEnablementStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def enable(self, workspace_key: str, server_id: str, config_hash: str) -> None:
        enabled_at = datetime.now(UTC).isoformat()
        with application_connection(self._database_path) as connection:
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
            connection.commit()

    def disable(self, workspace_key: str, server_id: str) -> None:
        with application_connection(self._database_path) as connection:
            connection.execute(
                "DELETE FROM mcp_enablements WHERE workspace_key = ? AND server_id = ?",
                (workspace_key, server_id),
            )
            connection.commit()

    def is_enabled(
        self,
        workspace_key: str,
        server_id: str,
        config_hash: str,
    ) -> bool:
        with application_connection(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT config_hash FROM mcp_enablements
                WHERE workspace_key = ? AND server_id = ?
                """,
                (workspace_key, server_id),
            ).fetchone()
        return row is not None and row["config_hash"] == config_hash
