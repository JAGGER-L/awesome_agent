from pathlib import Path

import pytest

from awesome_agent.attachments.models import AttachmentStorageError
from awesome_agent.attachments.store import AttachmentContentStore


def test_store_writes_basename_content_and_metadata(tmp_path: Path) -> None:
    store = AttachmentContentStore(tmp_path)

    stored = store.write(
        thread_id="thread-1",
        attachment_id="att-1",
        filename="../spec.md",
        content=b"# Spec\nUse attachments.\n",
        declared_mime_type="text/markdown",
    )

    assert stored.filename == "spec.md"
    assert stored.size == 24
    assert stored.media_type == "text"
    assert stored.sha256
    assert store.read_bytes(stored.storage_path) == b"# Spec\nUse attachments.\n"


def test_store_rejects_empty_or_unsafe_filename(tmp_path: Path) -> None:
    store = AttachmentContentStore(tmp_path)

    with pytest.raises(AttachmentStorageError, match="invalid_attachment_filename"):
        store.write(
            thread_id="thread-1",
            attachment_id="att-1",
            filename="",
            content=b"data",
            declared_mime_type=None,
        )


def test_store_rejects_oversized_file(tmp_path: Path) -> None:
    store = AttachmentContentStore(tmp_path, max_file_bytes=8)

    with pytest.raises(AttachmentStorageError, match="attachment_too_large"):
        store.write(
            thread_id="thread-1",
            attachment_id="att-1",
            filename="large.txt",
            content=b"0123456789",
            declared_mime_type="text/plain",
        )


def test_store_treats_non_utf8_as_binary(tmp_path: Path) -> None:
    store = AttachmentContentStore(tmp_path)

    stored = store.write(
        thread_id="thread-1",
        attachment_id="att-1",
        filename="blob.bin",
        content=b"\xff\x00\xfe",
        declared_mime_type="application/octet-stream",
    )

    assert stored.media_type == "binary"


def test_bounded_text_read_redacts_and_limits(tmp_path: Path) -> None:
    store = AttachmentContentStore(tmp_path)
    stored = store.write(
        thread_id="thread-1",
        attachment_id="att-1",
        filename="env.txt",
        content=b"line1\nOPENAI_API_KEY=sk-secretsecretsecret\nline3\n",
        declared_mime_type="text/plain",
    )

    result = store.read_text_range(
        stored.storage_path,
        start_line=1,
        max_lines=2,
        max_chars=40,
    )

    assert result.start_line == 1
    assert result.end_line == 2
    assert result.truncated is True
    assert "sk-secretsecretsecret" not in result.content
