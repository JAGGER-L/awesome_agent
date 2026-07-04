from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.attachments.models import AttachmentStatus, ThreadAttachment
from awesome_agent.persistence.models import ThreadAttachmentRecord


class PostgresAttachmentRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, attachment: ThreadAttachment) -> ThreadAttachment:
        async with self._sessions.begin() as session:
            session.add(_record_from_attachment(attachment))
        return attachment

    async def get(self, attachment_id: UUID) -> ThreadAttachment:
        async with self._sessions() as session:
            record = await session.get(ThreadAttachmentRecord, attachment_id)
        if record is None:
            raise KeyError(attachment_id)
        return _attachment_from_record(record)

    async def list_for_thread(
        self,
        thread_id: UUID,
        *,
        status: AttachmentStatus | None = None,
        include_deleted: bool = False,
        limit: int = 50,
    ) -> list[ThreadAttachment]:
        statement = select(ThreadAttachmentRecord).where(
            ThreadAttachmentRecord.thread_id == thread_id
        )
        if status is not None:
            statement = statement.where(ThreadAttachmentRecord.status == status.value)
        if not include_deleted:
            statement = statement.where(
                ThreadAttachmentRecord.status != AttachmentStatus.DELETED.value
            )
        statement = statement.order_by(
            ThreadAttachmentRecord.created_at.desc(),
            ThreadAttachmentRecord.id.desc(),
        ).limit(limit)
        async with self._sessions() as session:
            records = list(await session.scalars(statement))
        return [_attachment_from_record(record) for record in records]

    async def list_for_run(self, run_id: UUID) -> list[ThreadAttachment]:
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(ThreadAttachmentRecord)
                    .where(ThreadAttachmentRecord.run_id == run_id)
                    .where(
                        ThreadAttachmentRecord.status == AttachmentStatus.ATTACHED.value
                    )
                    .order_by(
                        ThreadAttachmentRecord.created_at,
                        ThreadAttachmentRecord.id,
                    )
                )
            )
        return [_attachment_from_record(record) for record in records]

    async def bind_pending_to_run(
        self,
        *,
        thread_id: UUID,
        attachment_ids: list[UUID],
        run_id: UUID,
        message_id: UUID,
    ) -> list[ThreadAttachment]:
        async with self._sessions.begin() as session:
            records: list[ThreadAttachmentRecord] = []
            for attachment_id in attachment_ids:
                record = await session.get(ThreadAttachmentRecord, attachment_id)
                if record is None:
                    raise KeyError(attachment_id)
                records.append(record)
            for record in records:
                if record.thread_id != thread_id:
                    raise ValueError("attachment_thread_mismatch")
                if record.status != AttachmentStatus.PENDING.value:
                    raise ValueError("attachment_not_pending")
            now = datetime.now(UTC)
            bound: list[ThreadAttachment] = []
            for record in records:
                attachment = _attachment_from_record(record).model_copy(
                    update={
                        "status": AttachmentStatus.ATTACHED,
                        "run_id": run_id,
                        "message_id": message_id,
                        "attached_at": now,
                    }
                )
                _apply_attachment(record, attachment)
                bound.append(attachment)
            return bound

    async def mark_deleted(self, attachment_id: UUID) -> ThreadAttachment:
        async with self._sessions.begin() as session:
            record = await session.get(ThreadAttachmentRecord, attachment_id)
            if record is None:
                raise KeyError(attachment_id)
            attachment = _attachment_from_record(record).model_copy(
                update={
                    "status": AttachmentStatus.DELETED,
                    "deleted_at": datetime.now(UTC),
                }
            )
            _apply_attachment(record, attachment)
            return attachment


def _record_from_attachment(attachment: ThreadAttachment) -> ThreadAttachmentRecord:
    return ThreadAttachmentRecord(
        id=attachment.id,
        thread_id=attachment.thread_id,
        run_id=attachment.run_id,
        message_id=attachment.message_id,
        status=attachment.status.value,
        payload=attachment.model_dump(mode="json"),
        created_at=attachment.created_at,
        attached_at=attachment.attached_at,
        deleted_at=attachment.deleted_at,
    )


def _apply_attachment(
    record: ThreadAttachmentRecord,
    attachment: ThreadAttachment,
) -> None:
    record.run_id = attachment.run_id
    record.message_id = attachment.message_id
    record.status = attachment.status.value
    record.payload = attachment.model_dump(mode="json")
    record.attached_at = attachment.attached_at
    record.deleted_at = attachment.deleted_at


def _attachment_from_record(record: ThreadAttachmentRecord) -> ThreadAttachment:
    return ThreadAttachment.model_validate(record.payload)
