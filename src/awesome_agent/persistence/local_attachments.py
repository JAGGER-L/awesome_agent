from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from awesome_agent.attachments.models import AttachmentStatus, ThreadAttachment


class LocalAttachmentRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        self._connection.close()

    async def create(self, attachment: ThreadAttachment) -> ThreadAttachment:
        self._upsert(attachment)
        return attachment

    async def get(self, attachment_id: UUID) -> ThreadAttachment:
        row = self._connection.execute(
            "SELECT payload_json FROM thread_attachments WHERE id = ?",
            (str(attachment_id),),
        ).fetchone()
        if row is None:
            raise KeyError(attachment_id)
        return ThreadAttachment.model_validate_json(row["payload_json"])

    async def list_for_thread(
        self,
        thread_id: UUID,
        *,
        status: AttachmentStatus | None = None,
        include_deleted: bool = False,
        limit: int = 50,
    ) -> list[ThreadAttachment]:
        clauses = ["thread_id = ?"]
        values: list[object] = [str(thread_id)]
        if status is not None:
            clauses.append("status = ?")
            values.append(status.value)
        if not include_deleted:
            clauses.append("status != ?")
            values.append(AttachmentStatus.DELETED.value)
        values.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT payload_json FROM thread_attachments
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            tuple(values),
        ).fetchall()
        return [
            ThreadAttachment.model_validate_json(row["payload_json"]) for row in rows
        ]

    async def list_for_run(self, run_id: UUID) -> list[ThreadAttachment]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM thread_attachments
            WHERE run_id = ? AND status = ?
            ORDER BY created_at ASC, id ASC
            """,
            (str(run_id), AttachmentStatus.ATTACHED.value),
        ).fetchall()
        return [
            ThreadAttachment.model_validate_json(row["payload_json"]) for row in rows
        ]

    async def bind_pending_to_run(
        self,
        *,
        thread_id: UUID,
        attachment_ids: list[UUID],
        run_id: UUID,
        message_id: UUID,
    ) -> list[ThreadAttachment]:
        with self._connection:
            selected = [
                await self.get(attachment_id) for attachment_id in attachment_ids
            ]
            for item in selected:
                if item.thread_id != thread_id:
                    raise ValueError("attachment_thread_mismatch")
                if item.status is not AttachmentStatus.PENDING:
                    raise ValueError("attachment_not_pending")
            now = datetime.now(UTC)
            bound = [
                item.model_copy(
                    update={
                        "status": AttachmentStatus.ATTACHED,
                        "run_id": run_id,
                        "message_id": message_id,
                        "attached_at": now,
                    }
                )
                for item in selected
            ]
            for item in bound:
                self._upsert(item)
        return bound

    async def mark_deleted(self, attachment_id: UUID) -> ThreadAttachment:
        item = await self.get(attachment_id)
        deleted = item.model_copy(
            update={
                "status": AttachmentStatus.DELETED,
                "deleted_at": datetime.now(UTC),
            }
        )
        self._upsert(deleted)
        return deleted

    def _upsert(self, attachment: ThreadAttachment) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO thread_attachments
                  (id, thread_id, run_id, status, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(attachment.id),
                    str(attachment.thread_id),
                    str(attachment.run_id) if attachment.run_id else None,
                    attachment.status.value,
                    attachment.model_dump_json(),
                    attachment.created_at.isoformat(),
                ),
            )

    def _ensure_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS thread_attachments (
                  id TEXT PRIMARY KEY,
                  thread_id TEXT NOT NULL,
                  run_id TEXT,
                  status TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_thread_attachments_thread_id
                ON thread_attachments(thread_id)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_thread_attachments_run_id
                ON thread_attachments(run_id)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_thread_attachments_status
                ON thread_attachments(status)
                """
            )
