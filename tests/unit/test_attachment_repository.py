from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from awesome_agent.attachments.models import (
    AttachmentMediaType,
    AttachmentScope,
    AttachmentSource,
    AttachmentStatus,
    ThreadAttachment,
)
from awesome_agent.attachments.repository import (
    AttachmentRepository,
    InMemoryAttachmentRepository,
)
from awesome_agent.persistence.local_attachments import LocalAttachmentRepository


def _attachment(thread_id: UUID) -> ThreadAttachment:
    return ThreadAttachment(
        thread_id=thread_id,
        scope=AttachmentScope.NEXT_TURN,
        status=AttachmentStatus.PENDING,
        filename="spec.md",
        mime_type="text/markdown",
        media_type=AttachmentMediaType.TEXT,
        size=12,
        sha256="a" * 64,
        storage_path=Path("attachments/thread/att/content"),
        source=AttachmentSource.TUI,
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda tmp_path: InMemoryAttachmentRepository(),
        lambda tmp_path: LocalAttachmentRepository(tmp_path / "state.db"),
    ],
)
@pytest.mark.asyncio
async def test_repository_create_list_get_delete_and_bind(
    tmp_path: Path,
    factory: Callable[[Path], AttachmentRepository],
) -> None:
    repository = factory(tmp_path)
    thread_id = uuid4()
    run_id = uuid4()
    message_id = uuid4()
    attachment = _attachment(thread_id)

    created = await repository.create(attachment)
    [listed] = await repository.list_for_thread(thread_id, include_deleted=False)
    bound = await repository.bind_pending_to_run(
        thread_id=thread_id,
        attachment_ids=[created.id],
        run_id=run_id,
        message_id=message_id,
    )
    deleted = await repository.mark_deleted(created.id)

    assert listed.id == created.id
    assert bound[0].status is AttachmentStatus.ATTACHED
    assert bound[0].run_id == run_id
    assert bound[0].message_id == message_id
    assert deleted.status is AttachmentStatus.DELETED


@pytest.mark.asyncio
async def test_bind_is_atomic_when_one_id_is_invalid(tmp_path: Path) -> None:
    repository = LocalAttachmentRepository(tmp_path / "state.db")
    thread_id = uuid4()
    attachment = await repository.create(_attachment(thread_id))

    with pytest.raises(KeyError):
        await repository.bind_pending_to_run(
            thread_id=thread_id,
            attachment_ids=[attachment.id, uuid4()],
            run_id=uuid4(),
            message_id=uuid4(),
        )

    reloaded = await repository.get(attachment.id)
    assert reloaded.status is AttachmentStatus.PENDING
    assert reloaded.run_id is None
