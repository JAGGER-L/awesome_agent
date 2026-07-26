from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from awesome_agent.config.resource_lock import exclusive_resource_lock
from awesome_agent.memory.models import (
    MemoryDocument,
    MemoryEntry,
    MemoryMutationResult,
    MemoryMutationStatus,
    MemoryScope,
)

START_MARKER = "<!-- awesome-agent:managed-memory:start -->"
END_MARKER = "<!-- awesome-agent:managed-memory:end -->"
_ID_PATTERN = re.compile(r"^<!-- memory:id=(memory_[a-f0-9]{32}) -->$")
_EMPTY_HASH = hashlib.sha256(b"").hexdigest()


class MemoryDocumentInvalid(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Local memory document is invalid: {code}")


@dataclass(frozen=True, slots=True)
class _ParsedDocument:
    document: MemoryDocument
    raw: bytes
    prefix: str
    suffix: str
    newline: str
    has_section: bool


class LocalMemoryFile:
    def __init__(
        self,
        *,
        path: Path,
        scope: MemoryScope,
        max_document_bytes: int = 1_000_000,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if max_document_bytes < 1:
            raise ValueError("max_document_bytes must be positive")
        self.path = path
        self.scope = scope
        self._max_document_bytes = max_document_bytes
        self._id_factory = id_factory or (lambda: f"memory_{uuid4().hex}")

    def snapshot(self) -> MemoryDocument:
        return self._read().document

    def add(self, content: str, *, expected_hash: str) -> MemoryMutationResult:
        with exclusive_resource_lock(self.path):
            parsed = self._read_for_mutation(expected_hash)
            if isinstance(parsed, MemoryMutationResult):
                return parsed
            entry_id = self._id_factory()
            entry = MemoryEntry(id=entry_id, content=content)
            if any(item.id == entry.id for item in parsed.document.entries):
                raise MemoryDocumentInvalid("duplicate_generated_id")
            return self._write_entries(
                parsed,
                (*parsed.document.entries, entry),
                status=MemoryMutationStatus.ADDED,
                entry_id=entry.id,
            )

    def replace(
        self,
        entry_id: str,
        content: str,
        *,
        expected_hash: str,
    ) -> MemoryMutationResult:
        with exclusive_resource_lock(self.path):
            parsed = self._read_for_mutation(expected_hash)
            if isinstance(parsed, MemoryMutationResult):
                return parsed
            existing = next(
                (item for item in parsed.document.entries if item.id == entry_id),
                None,
            )
            if existing is None:
                return self._not_found(parsed, entry_id)
            replacement = MemoryEntry(id=entry_id, content=content)
            entries = tuple(
                replacement if item.id == entry_id else item
                for item in parsed.document.entries
            )
            return self._write_entries(
                parsed,
                entries,
                status=MemoryMutationStatus.REPLACED,
                entry_id=entry_id,
            )

    def remove(
        self,
        entry_id: str,
        *,
        expected_hash: str,
    ) -> MemoryMutationResult:
        with exclusive_resource_lock(self.path):
            parsed = self._read_for_mutation(expected_hash)
            if isinstance(parsed, MemoryMutationResult):
                return parsed
            if all(item.id != entry_id for item in parsed.document.entries):
                return self._not_found(parsed, entry_id)
            entries = tuple(
                item for item in parsed.document.entries if item.id != entry_id
            )
            return self._write_entries(
                parsed,
                entries,
                status=MemoryMutationStatus.REMOVED,
                entry_id=entry_id,
            )

    def _read_for_mutation(
        self,
        expected_hash: str,
    ) -> _ParsedDocument | MemoryMutationResult:
        raw = self.path.read_bytes() if self.path.exists() else b""
        current_hash = _hash(raw)
        if current_hash != expected_hash:
            return MemoryMutationResult(
                status=MemoryMutationStatus.CONFLICT,
                scope=self.scope,
                content_hash=current_hash,
                error_code=MemoryMutationStatus.CONFLICT.value,
            )
        return self._parse(raw)

    def _read(self) -> _ParsedDocument:
        raw = self.path.read_bytes() if self.path.exists() else b""
        return self._parse(raw)

    def _parse(self, raw: bytes) -> _ParsedDocument:
        if len(raw) > self._max_document_bytes:
            raise MemoryDocumentInvalid("document_too_large")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MemoryDocumentInvalid("invalid_utf8") from error
        lines = text.splitlines(keepends=True)
        starts = _marker_lines(lines, START_MARKER, text)
        ends = _marker_lines(lines, END_MARKER, text)
        if not starts and not ends:
            document = _document(self.scope, self.path, raw, text, ())
            return _ParsedDocument(
                document=document,
                raw=raw,
                prefix=text,
                suffix="",
                newline=_preferred_newline(text),
                has_section=False,
            )
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise MemoryDocumentInvalid("managed_markers")
        start = starts[0]
        end = ends[0]
        entries = _parse_entries(lines[start + 1 : end])
        newline = _line_ending(lines[start]) or _preferred_newline(text)
        document = _document(self.scope, self.path, raw, text, entries)
        return _ParsedDocument(
            document=document,
            raw=raw,
            prefix="".join(lines[:start]),
            suffix="".join(lines[end + 1 :]),
            newline=newline,
            has_section=True,
        )

    def _write_entries(
        self,
        parsed: _ParsedDocument,
        entries: tuple[MemoryEntry, ...],
        *,
        status: MemoryMutationStatus,
        entry_id: str,
    ) -> MemoryMutationResult:
        section = _render_section(entries, parsed.newline)
        prefix = parsed.prefix
        if not parsed.has_section and prefix and not prefix.endswith(("\n", "\r")):
            prefix = f"{prefix}{parsed.newline}"
        raw = f"{prefix}{section}{parsed.suffix}".encode()
        if len(raw) > self._max_document_bytes:
            raise MemoryDocumentInvalid("document_too_large")
        document = self._parse(raw).document
        current_raw = self.path.read_bytes() if self.path.exists() else b""
        current_hash = _hash(current_raw)
        if current_hash != parsed.document.content_hash:
            return MemoryMutationResult(
                status=MemoryMutationStatus.CONFLICT,
                scope=self.scope,
                content_hash=current_hash,
                error_code=MemoryMutationStatus.CONFLICT.value,
            )
        _atomic_replace(self.path, raw)
        return MemoryMutationResult(
            status=status,
            scope=self.scope,
            entry_id=entry_id,
            content_hash=document.content_hash,
            document=document,
        )

    def _not_found(
        self,
        parsed: _ParsedDocument,
        entry_id: str,
    ) -> MemoryMutationResult:
        return MemoryMutationResult(
            status=MemoryMutationStatus.NOT_FOUND,
            scope=self.scope,
            entry_id=entry_id,
            content_hash=parsed.document.content_hash,
            document=parsed.document,
            error_code=MemoryMutationStatus.NOT_FOUND.value,
        )


def render_memory_document(document: MemoryDocument) -> bytes:
    """Return the exact decoded document bytes for a no-op round trip."""

    return document.markdown.encode("utf-8")


def _marker_lines(lines: list[str], marker: str, text: str) -> list[int]:
    exact = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == marker]
    if text.count(marker) != len(exact):
        raise MemoryDocumentInvalid("managed_marker_line")
    return exact


def _parse_entries(lines: list[str]) -> tuple[MemoryEntry, ...]:
    entries: list[MemoryEntry] = []
    current_id: str | None = None
    content_lines: list[str] = []

    def finish() -> None:
        nonlocal current_id, content_lines
        if current_id is None:
            if any(line.strip() for line in content_lines):
                raise MemoryDocumentInvalid("managed_content_without_id")
            content_lines = []
            return
        if not content_lines:
            raise MemoryDocumentInvalid("entry_content_missing")
        first = content_lines[0].rstrip("\r\n")
        if not first.startswith("- "):
            raise MemoryDocumentInvalid("entry_bullet_missing")
        content = "\n".join(
            [first[2:], *(line.rstrip("\r\n") for line in content_lines[1:])]
        ).rstrip()
        try:
            entry = MemoryEntry(id=current_id, content=content)
        except ValueError as error:
            raise MemoryDocumentInvalid("entry_invalid") from error
        if any(item.id == entry.id for item in entries):
            raise MemoryDocumentInvalid("duplicate_entry_id")
        entries.append(entry)
        current_id = None
        content_lines = []

    for line in lines:
        body = line.rstrip("\r\n")
        match = _ID_PATTERN.fullmatch(body)
        if match is not None:
            finish()
            current_id = match.group(1)
            continue
        if body.startswith("<!-- memory:id="):
            raise MemoryDocumentInvalid("entry_id_invalid")
        content_lines.append(line)
    finish()
    return tuple(entries)


def _render_section(entries: tuple[MemoryEntry, ...], newline: str) -> str:
    lines = [START_MARKER]
    for entry in entries:
        lines.append(f"<!-- memory:id={entry.id} -->")
        content_lines = entry.content.split("\n")
        lines.append(f"- {content_lines[0]}")
        lines.extend(content_lines[1:])
    lines.append(END_MARKER)
    return f"{newline.join(lines)}{newline}"


def _document(
    scope: MemoryScope,
    path: Path,
    raw: bytes,
    markdown: str,
    entries: tuple[MemoryEntry, ...],
) -> MemoryDocument:
    return MemoryDocument(
        scope=scope,
        path=path,
        content_hash=_hash(raw) if raw else _EMPTY_HASH,
        markdown=markdown,
        entries=entries,
    )


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _preferred_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _atomic_replace(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()
