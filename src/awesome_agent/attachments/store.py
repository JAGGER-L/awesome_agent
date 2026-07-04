from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path

from awesome_agent.attachments.models import (
    AttachmentMediaType,
    AttachmentStorageError,
    AttachmentTextRead,
    StoredAttachmentContent,
)
from awesome_agent.safety.redaction import redact_text


class AttachmentContentStore:
    def __init__(self, root: Path, *, max_file_bytes: int = 5 * 1024 * 1024) -> None:
        self._root = root
        self._max_file_bytes = max_file_bytes

    @property
    def root(self) -> Path:
        return self._root

    def write(
        self,
        *,
        thread_id: str,
        attachment_id: str,
        filename: str,
        content: bytes,
        declared_mime_type: str | None,
    ) -> StoredAttachmentContent:
        safe_name = _safe_filename(filename)
        if len(content) > self._max_file_bytes:
            raise AttachmentStorageError(
                "attachment_too_large",
                f"Maximum attachment size is {self._max_file_bytes} bytes.",
            )
        media_type = _media_type(content)
        mime_type = declared_mime_type or mimetypes.guess_type(safe_name)[0]
        mime_type = mime_type or "application/octet-stream"
        digest = hashlib.sha256(content).hexdigest()
        directory = self._root / thread_id / attachment_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "content"
        path.write_bytes(content)
        return StoredAttachmentContent(
            filename=safe_name,
            mime_type=mime_type,
            media_type=media_type,
            size=len(content),
            sha256=digest,
            storage_path=path,
        )

    def read_bytes(self, path: Path) -> bytes:
        return _resolved_inside(self._root, path).read_bytes()

    def delete_content(self, path: Path) -> None:
        resolved = _resolved_inside(self._root, path)
        resolved.unlink(missing_ok=True)

    def read_text_range(
        self,
        path: Path,
        *,
        start_line: int,
        max_lines: int,
        max_chars: int,
    ) -> AttachmentTextRead:
        raw = self.read_bytes(path)
        if _media_type(raw) is not AttachmentMediaType.TEXT:
            raise AttachmentStorageError(
                "attachment_not_text_like",
                "Attachment is not UTF-8 text.",
            )
        text = raw.decode("utf-8")
        lines = text.splitlines()
        if start_line < 1 or start_line > max(len(lines), 1):
            raise AttachmentStorageError(
                "attachment_read_out_of_range",
                "Requested start line is outside the attachment.",
            )
        selected = lines[start_line - 1 : start_line - 1 + max_lines]
        content = "\n".join(selected)
        truncated = (
            len(selected) < len(lines[start_line - 1 :]) or len(content) > max_chars
        )
        if len(content) > max_chars:
            content = content[:max_chars]
        redacted = redact_text(content)
        return AttachmentTextRead(
            content=redacted.text,
            start_line=start_line,
            end_line=start_line + len(selected) - 1,
            total_lines=len(lines),
            truncated=truncated,
            redacted=redacted.redacted,
        )


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    if not name or name in {".", ".."} or len(name) > 255:
        raise AttachmentStorageError(
            "invalid_attachment_filename",
            "Attachment filename is invalid.",
        )
    return name


def _media_type(content: bytes) -> AttachmentMediaType:
    if b"\x00" in content:
        return AttachmentMediaType.BINARY
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return AttachmentMediaType.BINARY
    return AttachmentMediaType.TEXT


def _resolved_inside(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and not resolved.is_relative_to(resolved_root):
        raise AttachmentStorageError(
            "invalid_attachment_path",
            "Attachment path escapes the attachment store.",
        )
    if resolved.is_symlink() or os.path.islink(resolved):
        raise AttachmentStorageError(
            "invalid_attachment_path",
            "Symlink attachments are not supported.",
        )
    return resolved
