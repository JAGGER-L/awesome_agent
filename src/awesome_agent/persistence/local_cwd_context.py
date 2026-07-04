from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from awesome_agent.runtime.cwd_context import CwdContextSnapshot


class LocalCwdContextSnapshotRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        self._connection.close()

    async def latest_for_thread(
        self,
        thread_id: UUID,
        working_directory: str,
    ) -> CwdContextSnapshot | None:
        row = self._connection.execute(
            """
            SELECT payload_json FROM cwd_context_snapshots
            WHERE thread_id = ? AND working_directory = ?
            ORDER BY created_at DESC, snapshot_id DESC
            LIMIT 1
            """,
            (str(thread_id), working_directory),
        ).fetchone()
        if row is None:
            return None
        return CwdContextSnapshot.model_validate_json(row["payload_json"])

    async def save(self, snapshot: CwdContextSnapshot) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO cwd_context_snapshots
                  (
                    snapshot_id,
                    thread_id,
                    working_directory,
                    status,
                    payload_json,
                    created_at
                  )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.id,
                    str(snapshot.thread_id),
                    snapshot.working_directory,
                    snapshot.status,
                    snapshot.model_dump_json(),
                    snapshot.created_at.isoformat(),
                ),
            )

    def _ensure_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cwd_context_snapshots (
                  snapshot_id TEXT PRIMARY KEY,
                  thread_id TEXT NOT NULL,
                  working_directory TEXT NOT NULL,
                  status TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cwd_context_thread_dir_created
                ON cwd_context_snapshots (thread_id, working_directory, created_at)
                """
            )
