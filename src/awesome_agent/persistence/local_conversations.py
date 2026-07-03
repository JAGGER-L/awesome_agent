from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from awesome_agent.conversation.models import (
    ThreadMessage,
    ThreadMessageKind,
    ThreadMessageRole,
)
from awesome_agent.domain.threads import Thread

_SCHEMA_VERSION = "1"


class LocalConversationRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()

    def close(self) -> None:
        self._connection.close()

    async def create_thread(
        self,
        *,
        title: str,
        context_kind: str = "workspace",
        context_path: str | None = None,
        repository_id: UUID | None = None,
        default_model: str | None = None,
        sandbox_profile: str | None = None,
        thinking_mode: str | None = None,
        local_memory_enabled: bool = False,
        provider_memory: str | None = None,
    ) -> Thread:
        thread = Thread(
            title=title,
            context_kind=context_kind,
            context_path=context_path,
            repository_id=repository_id,
            default_model=default_model,
            sandbox_profile=sandbox_profile,
            thinking_mode=thinking_mode,
            local_memory_enabled=local_memory_enabled,
            provider_memory=provider_memory,
        )
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO threads (
                  id, title, context_kind, context_path, repository_id,
                  default_model, sandbox_profile, thinking_mode,
                  local_memory_enabled, provider_memory, changed_file_count,
                  changed_files_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _thread_values(thread, changed_file_count=0, changed_files=[]),
            )
        return thread

    async def list_threads(self) -> list[Thread]:
        rows = self._connection.execute(
            """
            SELECT * FROM threads
            ORDER BY updated_at DESC, created_at DESC, id DESC
            """
        ).fetchall()
        return [_thread_from_row(row) for row in rows]

    async def get_thread(self, thread_id: UUID) -> Thread:
        row = self._connection.execute(
            "SELECT * FROM threads WHERE id = ?",
            (str(thread_id),),
        ).fetchone()
        if row is None:
            raise KeyError(f"Thread not found: {thread_id}")
        return _thread_from_row(row)

    async def bind_repository(self, thread_id: UUID, repository_id: UUID) -> Thread:
        await self.get_thread(thread_id)
        with self._connection:
            self._connection.execute(
                "UPDATE threads SET repository_id = ? WHERE id = ?",
                (str(repository_id), str(thread_id)),
            )
        return await self.get_thread(thread_id)

    async def update_thread_settings(
        self,
        thread_id: UUID,
        *,
        default_model: str | None = None,
        thinking_mode: str | None = None,
        local_memory_enabled: bool | None = None,
        provider_memory: str | None = None,
    ) -> Thread:
        thread = await self.get_thread(thread_id)
        updated = thread.model_copy(
            update={
                "default_model": default_model
                if default_model is not None
                else thread.default_model,
                "thinking_mode": thinking_mode
                if thinking_mode is not None
                else thread.thinking_mode,
                "local_memory_enabled": local_memory_enabled
                if local_memory_enabled is not None
                else thread.local_memory_enabled,
                "provider_memory": provider_memory,
            }
        )
        with self._connection:
            self._connection.execute(
                """
                UPDATE threads
                SET default_model = ?, thinking_mode = ?,
                    local_memory_enabled = ?, provider_memory = ?
                WHERE id = ?
                """,
                (
                    updated.default_model,
                    updated.thinking_mode,
                    int(updated.local_memory_enabled),
                    updated.provider_memory,
                    str(thread_id),
                ),
            )
        return updated

    async def resolve_thread(self, query: str) -> Thread:
        try:
            return await self.get_thread(UUID(query))
        except (ValueError, KeyError):
            pass
        normalized = f"%{query.casefold()}%"
        row = self._connection.execute(
            """
            SELECT * FROM threads
            WHERE lower(title) LIKE ?
            ORDER BY updated_at DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Thread not found: {query}")
        return _thread_from_row(row)

    async def append_message(
        self,
        *,
        thread_id: UUID,
        role: ThreadMessageRole,
        content: str,
        kind: ThreadMessageKind = ThreadMessageKind.MESSAGE,
        run_id: UUID | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ThreadMessage:
        await self.get_thread(thread_id)
        sequence = (
            int(
                self._connection.execute(
                    """
                SELECT COALESCE(MAX(sequence), 0)
                FROM thread_messages
                WHERE thread_id = ?
                """,
                    (str(thread_id),),
                ).fetchone()[0]
            )
            + 1
        )
        message = ThreadMessage(
            thread_id=thread_id,
            role=role,
            content=content,
            kind=kind,
            run_id=run_id,
            metadata=metadata or {},
            sequence=sequence,
        )
        changed_files = _changed_files_from_metadata(message.metadata)
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO thread_messages (
                  id, thread_id, role, content, kind, run_id, metadata_json,
                  sequence, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(message.id),
                    str(message.thread_id),
                    message.role.value,
                    message.content,
                    message.kind.value,
                    str(message.run_id) if message.run_id is not None else None,
                    json.dumps(message.metadata, default=str),
                    message.sequence,
                    message.created_at.isoformat(),
                ),
            )
            self._connection.execute(
                """
                UPDATE threads
                SET updated_at = ?,
                    changed_file_count = CASE WHEN ? > 0
                        THEN ?
                        ELSE changed_file_count
                    END,
                    changed_files_json = CASE WHEN ? > 0
                        THEN ?
                        ELSE changed_files_json
                    END
                WHERE id = ?
                """,
                (
                    message.created_at.isoformat(),
                    len(changed_files),
                    len(changed_files),
                    len(changed_files),
                    json.dumps(changed_files, default=str),
                    str(thread_id),
                ),
            )
        return message

    async def list_messages(self, thread_id: UUID) -> list[ThreadMessage]:
        await self.get_thread(thread_id)
        rows = self._connection.execute(
            """
            SELECT * FROM thread_messages
            WHERE thread_id = ?
            ORDER BY sequence
            """,
            (str(thread_id),),
        ).fetchall()
        return [_message_from_row(row) for row in rows]

    def _ensure_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                )
                """
            )
            existing_version = self._connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            if existing_version is not None and existing_version[0] != _SCHEMA_VERSION:
                raise RuntimeError(
                    "Local state database is from a newer awesome_agent version."
                )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS threads (
                  id TEXT PRIMARY KEY,
                  title TEXT NOT NULL,
                  context_kind TEXT NOT NULL,
                  context_path TEXT,
                  repository_id TEXT,
                  default_model TEXT,
                  sandbox_profile TEXT,
                  thinking_mode TEXT,
                  local_memory_enabled INTEGER NOT NULL DEFAULT 0,
                  provider_memory TEXT,
                  changed_file_count INTEGER NOT NULL DEFAULT 0,
                  changed_files_json TEXT NOT NULL DEFAULT '[]',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS thread_messages (
                  id TEXT PRIMARY KEY,
                  thread_id TEXT NOT NULL,
                  role TEXT NOT NULL,
                  content TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  run_id TEXT,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  sequence INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(thread_id) REFERENCES threads(id)
                )
                """
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO metadata (key, value)
                VALUES ('schema_version', ?)
                """,
                (_SCHEMA_VERSION,),
            )


def _thread_values(
    thread: Thread,
    *,
    changed_file_count: int,
    changed_files: list[dict[str, object]],
) -> tuple[object, ...]:
    return (
        str(thread.id),
        thread.title,
        thread.context_kind,
        thread.context_path,
        str(thread.repository_id) if thread.repository_id is not None else None,
        thread.default_model,
        thread.sandbox_profile,
        thread.thinking_mode,
        int(thread.local_memory_enabled),
        thread.provider_memory,
        changed_file_count,
        json.dumps(changed_files, default=str),
        thread.created_at.isoformat(),
        thread.updated_at.isoformat(),
    )


def _thread_from_row(row: sqlite3.Row) -> Thread:
    repository_id = row["repository_id"]
    return Thread(
        id=UUID(row["id"]),
        title=row["title"],
        context_kind=row["context_kind"],
        context_path=row["context_path"],
        repository_id=UUID(repository_id) if repository_id else None,
        default_model=row["default_model"],
        sandbox_profile=row["sandbox_profile"],
        thinking_mode=row["thinking_mode"],
        local_memory_enabled=bool(row["local_memory_enabled"]),
        provider_memory=row["provider_memory"],
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


def _message_from_row(row: sqlite3.Row) -> ThreadMessage:
    run_id = row["run_id"]
    return ThreadMessage(
        id=UUID(row["id"]),
        thread_id=UUID(row["thread_id"]),
        role=ThreadMessageRole(row["role"]),
        content=row["content"],
        kind=ThreadMessageKind(row["kind"]),
        run_id=UUID(run_id) if run_id else None,
        metadata=json.loads(row["metadata_json"] or "{}"),
        sequence=row["sequence"],
        created_at=_parse_datetime(row["created_at"]),
    )


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _changed_files_from_metadata(
    metadata: dict[str, object],
) -> list[dict[str, object]]:
    value = metadata.get("changed_files")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
