from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from awesome_agent.attachments.models import (
    AttachmentContextItem,
    AttachmentContextSnapshot,
    AttachmentMediaType,
    AttachmentScope,
    AttachmentSource,
    AttachmentStatus,
    AttachmentTextRead,
    ThreadAttachment,
)
from awesome_agent.attachments.repository import AttachmentRepository
from awesome_agent.attachments.store import AttachmentContentStore


class AttachmentService:
    def __init__(
        self,
        *,
        repository: AttachmentRepository,
        store: AttachmentContentStore,
        max_pending_per_thread: int = 5,
        max_per_turn: int = 5,
        context_file_chars: int = 16_000,
        context_total_chars: int = 48_000,
        read_max_lines: int = 500,
        read_max_chars: int = 30_000,
    ) -> None:
        self.repository = repository
        self.store = store
        self.max_pending_per_thread = max_pending_per_thread
        self.max_per_turn = max_per_turn
        self.context_file_chars = context_file_chars
        self.context_total_chars = context_total_chars
        self.read_max_lines = read_max_lines
        self.read_max_chars = read_max_chars

    async def create(
        self,
        *,
        thread_id: UUID,
        filename: str,
        content: bytes,
        mime_type: str | None,
        source: AttachmentSource,
        scope: AttachmentScope = AttachmentScope.NEXT_TURN,
    ) -> ThreadAttachment:
        if scope is not AttachmentScope.NEXT_TURN:
            raise ValueError("unsupported_attachment_scope")
        pending = await self.repository.list_for_thread(
            thread_id,
            status=AttachmentStatus.PENDING,
            include_deleted=False,
            limit=self.max_pending_per_thread + 1,
        )
        if len(pending) >= self.max_pending_per_thread:
            raise ValueError("too_many_pending_attachments")
        attachment_id = uuid4()
        stored = self.store.write(
            thread_id=str(thread_id),
            attachment_id=str(attachment_id),
            filename=filename,
            content=content,
            declared_mime_type=mime_type,
        )
        attachment = ThreadAttachment(
            id=attachment_id,
            thread_id=thread_id,
            scope=scope,
            status=AttachmentStatus.PENDING,
            filename=stored.filename,
            mime_type=stored.mime_type,
            media_type=stored.media_type,
            size=stored.size,
            sha256=stored.sha256,
            storage_path=stored.storage_path,
            source=source,
        )
        return await self.repository.create(attachment)

    async def create_from_path(
        self,
        *,
        thread_id: UUID,
        path: Path,
        source: AttachmentSource,
    ) -> ThreadAttachment:
        resolved = path.resolve()
        if not resolved.is_file() or resolved.is_symlink():
            raise ValueError("invalid_attachment_path")
        return await self.create(
            thread_id=thread_id,
            filename=resolved.name,
            content=resolved.read_bytes(),
            mime_type=None,
            source=source,
        )

    async def list_thread(
        self,
        thread_id: UUID,
        *,
        status: AttachmentStatus | None = None,
        include_deleted: bool = False,
        limit: int = 50,
    ) -> list[ThreadAttachment]:
        return await self.repository.list_for_thread(
            thread_id,
            status=status,
            include_deleted=include_deleted,
            limit=limit,
        )

    async def get(self, *, thread_id: UUID, attachment_id: UUID) -> ThreadAttachment:
        try:
            attachment = await self.repository.get(attachment_id)
        except KeyError as error:
            raise ValueError("attachment_not_found") from error
        if attachment.thread_id != thread_id:
            raise ValueError("attachment_thread_mismatch")
        return attachment

    async def delete(self, *, thread_id: UUID, attachment_id: UUID) -> ThreadAttachment:
        attachment = await self.get(thread_id=thread_id, attachment_id=attachment_id)
        if attachment.status is not AttachmentStatus.DELETED:
            self.store.delete_content(attachment.storage_path)
        return await self.repository.mark_deleted(attachment_id)

    async def bind_to_run(
        self,
        *,
        thread_id: UUID,
        attachment_ids: list[UUID],
        run_id: UUID,
        message_id: UUID,
    ) -> list[ThreadAttachment]:
        if len(attachment_ids) > self.max_per_turn:
            raise ValueError("too_many_turn_attachments")
        if not attachment_ids:
            return []
        try:
            return await self.repository.bind_pending_to_run(
                thread_id=thread_id,
                attachment_ids=attachment_ids,
                run_id=run_id,
                message_id=message_id,
            )
        except KeyError as error:
            raise ValueError("attachment_not_found") from error

    async def list_for_tool(self, *, run_id: UUID) -> list[ThreadAttachment]:
        return await self.repository.list_for_run(run_id)

    async def build_context(self, run_id: UUID) -> AttachmentContextSnapshot:
        remaining = self.context_total_chars
        items: list[AttachmentContextItem] = []
        for attachment in await self.repository.list_for_run(run_id):
            if attachment.status is AttachmentStatus.DELETED:
                continue
            content: str | None = None
            injected_chars = 0
            truncated = False
            redacted = False
            if attachment.media_type is AttachmentMediaType.TEXT:
                limit = max(0, min(self.context_file_chars, remaining))
                if limit > 0 and attachment.storage_path.exists():
                    read = self.store.read_text_range(
                        attachment.storage_path,
                        start_line=1,
                        max_lines=self.read_max_lines,
                        max_chars=limit,
                    )
                    content = read.content
                    injected_chars = len(content)
                    truncated = read.truncated
                    redacted = read.redacted
                    remaining -= injected_chars
                else:
                    truncated = True
            items.append(
                AttachmentContextItem(
                    attachment_id=attachment.id,
                    filename=attachment.filename,
                    mime_type=attachment.mime_type,
                    media_type=attachment.media_type,
                    size=attachment.size,
                    sha256=attachment.sha256,
                    injected_chars=injected_chars,
                    truncated=truncated,
                    redacted=redacted,
                    content=content,
                )
            )
        return AttachmentContextSnapshot(run_id=run_id, items=items)

    async def read_for_tool(
        self,
        *,
        run_id: UUID,
        attachment_id: UUID,
        start_line: int,
        max_lines: int,
        max_chars: int,
    ) -> AttachmentTextRead:
        attachments = await self.repository.list_for_run(run_id)
        attachment = next(
            (item for item in attachments if item.id == attachment_id),
            None,
        )
        if attachment is None:
            raise ValueError("attachment_not_bound_to_run")
        if attachment.status is AttachmentStatus.DELETED:
            raise ValueError("attachment_deleted")
        if not attachment.storage_path.exists():
            raise ValueError("attachment_content_deleted")
        return self.store.read_text_range(
            attachment.storage_path,
            start_line=start_line,
            max_lines=min(max_lines, self.read_max_lines),
            max_chars=min(max_chars, self.read_max_chars),
        )
