from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from awesome_agent.attachments.models import AttachmentStatus, ThreadAttachment


class AttachmentRepository(Protocol):
    async def create(self, attachment: ThreadAttachment) -> ThreadAttachment: ...

    async def get(self, attachment_id: UUID) -> ThreadAttachment: ...

    async def list_for_thread(
        self,
        thread_id: UUID,
        *,
        status: AttachmentStatus | None = None,
        include_deleted: bool = False,
        limit: int = 50,
    ) -> list[ThreadAttachment]: ...

    async def list_for_run(self, run_id: UUID) -> list[ThreadAttachment]: ...

    async def bind_pending_to_run(
        self,
        *,
        thread_id: UUID,
        attachment_ids: list[UUID],
        run_id: UUID,
        message_id: UUID,
    ) -> list[ThreadAttachment]: ...

    async def mark_deleted(self, attachment_id: UUID) -> ThreadAttachment: ...


class InMemoryAttachmentRepository:
    def __init__(self) -> None:
        self._items: dict[UUID, ThreadAttachment] = {}

    async def create(self, attachment: ThreadAttachment) -> ThreadAttachment:
        self._items[attachment.id] = attachment
        return attachment

    async def get(self, attachment_id: UUID) -> ThreadAttachment:
        try:
            return self._items[attachment_id]
        except KeyError as error:
            raise KeyError(attachment_id) from error

    async def list_for_thread(
        self,
        thread_id: UUID,
        *,
        status: AttachmentStatus | None = None,
        include_deleted: bool = False,
        limit: int = 50,
    ) -> list[ThreadAttachment]:
        items = [
            item
            for item in self._items.values()
            if item.thread_id == thread_id
            and (include_deleted or item.status is not AttachmentStatus.DELETED)
            and (status is None or item.status is status)
        ]
        return sorted(items, key=lambda item: item.created_at, reverse=True)[:limit]

    async def list_for_run(self, run_id: UUID) -> list[ThreadAttachment]:
        return [
            item
            for item in sorted(self._items.values(), key=lambda value: value.created_at)
            if item.run_id == run_id and item.status is AttachmentStatus.ATTACHED
        ]

    async def bind_pending_to_run(
        self,
        *,
        thread_id: UUID,
        attachment_ids: list[UUID],
        run_id: UUID,
        message_id: UUID,
    ) -> list[ThreadAttachment]:
        selected = [await self.get(attachment_id) for attachment_id in attachment_ids]
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
            self._items[item.id] = item
        return bound

    async def mark_deleted(self, attachment_id: UUID) -> ThreadAttachment:
        item = await self.get(attachment_id)
        deleted = item.model_copy(
            update={
                "status": AttachmentStatus.DELETED,
                "deleted_at": datetime.now(UTC),
            }
        )
        self._items[attachment_id] = deleted
        return deleted
