from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.attachments.models import AttachmentSource, AttachmentStatus
from awesome_agent.attachments.repository import InMemoryAttachmentRepository
from awesome_agent.attachments.service import AttachmentService
from awesome_agent.attachments.store import AttachmentContentStore


def _service(tmp_path: Path) -> AttachmentService:
    return AttachmentService(
        repository=InMemoryAttachmentRepository(),
        store=AttachmentContentStore(tmp_path / "attachments"),
    )


@pytest.mark.asyncio
async def test_create_list_delete_pending_attachment(tmp_path: Path) -> None:
    service = _service(tmp_path)
    thread_id = uuid4()

    created = await service.create(
        thread_id=thread_id,
        filename="spec.md",
        content=b"# Spec\n",
        mime_type="text/markdown",
        source=AttachmentSource.TUI,
    )
    listed = await service.list_thread(thread_id)
    deleted = await service.delete(thread_id=thread_id, attachment_id=created.id)

    assert listed[0].id == created.id
    assert deleted.status is AttachmentStatus.DELETED
    assert not created.storage_path.exists()


@pytest.mark.asyncio
async def test_bind_is_explicit_and_atomic(tmp_path: Path) -> None:
    service = _service(tmp_path)
    thread_id = uuid4()
    first = await service.create(
        thread_id=thread_id,
        filename="a.txt",
        content=b"a",
        mime_type="text/plain",
        source=AttachmentSource.API,
    )

    with pytest.raises(ValueError, match="attachment_not_found"):
        await service.bind_to_run(
            thread_id=thread_id,
            attachment_ids=[first.id, uuid4()],
            run_id=uuid4(),
            message_id=uuid4(),
        )

    assert (
        await service.get(thread_id=thread_id, attachment_id=first.id)
    ).status is AttachmentStatus.PENDING


@pytest.mark.asyncio
async def test_context_injects_text_and_metadata_only_for_binary(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    thread_id = uuid4()
    run_id = uuid4()
    message_id = uuid4()
    text = await service.create(
        thread_id=thread_id,
        filename="spec.md",
        content=b"# Spec\nUse this.\n",
        mime_type="text/markdown",
        source=AttachmentSource.API,
    )
    binary = await service.create(
        thread_id=thread_id,
        filename="image.png",
        content=b"\x89PNG\x00",
        mime_type="image/png",
        source=AttachmentSource.API,
    )
    await service.bind_to_run(
        thread_id=thread_id,
        attachment_ids=[text.id, binary.id],
        run_id=run_id,
        message_id=message_id,
    )

    snapshot = await service.build_context(run_id)

    rendered = snapshot.render()
    assert "# Spec" in rendered
    assert "image.png" in rendered
    assert "\x00" not in rendered


@pytest.mark.asyncio
async def test_read_for_tool_only_reads_run_bound_text(tmp_path: Path) -> None:
    service = _service(tmp_path)
    thread_id = uuid4()
    other_run_id = uuid4()
    attachment = await service.create(
        thread_id=thread_id,
        filename="spec.md",
        content=b"one\ntwo\nthree\n",
        mime_type="text/plain",
        source=AttachmentSource.API,
    )

    with pytest.raises(ValueError, match="attachment_not_bound_to_run"):
        await service.read_for_tool(
            run_id=other_run_id,
            attachment_id=attachment.id,
            start_line=1,
            max_lines=10,
            max_chars=100,
        )
