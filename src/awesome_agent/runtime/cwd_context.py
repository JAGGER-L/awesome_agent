from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field

CONTEXT_FILENAMES = ("AGENTS.md", "CLAUDE.md")
MAX_CONTEXT_FILE_BYTES = 128 * 1024
PRECEDENCE = "AGENTS.md > CLAUDE.md"
CONFLICT_POLICY = "CLAUDE.md cannot override AGENTS.md on conflict."


class CwdContextFileSnapshot(BaseModel):
    filename: str
    path: str
    exists: bool
    size_bytes: int | None = None
    mtime_ns: int | None = None
    sha256: str | None = None
    included: bool = False
    skipped_reason: str | None = None
    original_lines: int = 0
    injected_lines: int = 0
    deduped_lines: int = 0


class CwdContextSnapshot(BaseModel):
    id: str
    thread_id: UUID
    working_directory: str
    status: str
    precedence: str = PRECEDENCE
    conflict_policy: str = CONFLICT_POLICY
    files: list[CwdContextFileSnapshot] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CwdContextEvaluation(BaseModel):
    status: str
    snapshot: CwdContextSnapshot | None
    rendered: str
    evidence: dict[str, object]


class CwdContextSnapshotRepository(Protocol):
    async def latest_for_thread(
        self,
        thread_id: UUID,
        working_directory: str,
    ) -> CwdContextSnapshot | None: ...

    async def save(self, snapshot: CwdContextSnapshot) -> None: ...


class InMemoryCwdContextSnapshotRepository:
    def __init__(self) -> None:
        self._items: list[CwdContextSnapshot] = []

    async def latest_for_thread(
        self,
        thread_id: UUID,
        working_directory: str,
    ) -> CwdContextSnapshot | None:
        matches = [
            item
            for item in self._items
            if item.thread_id == thread_id
            and item.working_directory == working_directory
        ]
        return matches[-1] if matches else None

    async def save(self, snapshot: CwdContextSnapshot) -> None:
        if all(item.id != snapshot.id for item in self._items):
            self._items.append(snapshot)


class CwdContextService:
    def __init__(
        self,
        *,
        repository: CwdContextSnapshotRepository,
        max_file_bytes: int = MAX_CONTEXT_FILE_BYTES,
    ) -> None:
        self._repository = repository
        self._max_file_bytes = max_file_bytes

    async def evaluate(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        working_directory: Path | None,
    ) -> CwdContextEvaluation:
        if working_directory is None:
            return _disabled("disabled_no_working_directory", run_id=run_id)
        directory = Path(working_directory)
        if not directory.exists() or not directory.is_dir():
            return _disabled(
                "disabled_invalid_working_directory",
                run_id=run_id,
                working_directory=str(directory),
            )

        files, rendered_parts = self._read_files(directory)
        has_context_result = any(
            file.exists and (file.included or file.skipped_reason) for file in files
        )
        initial_status = "created" if has_context_result else "none_found"
        snapshot = CwdContextSnapshot(
            id=_snapshot_id(thread_id, directory, files),
            thread_id=thread_id,
            working_directory=str(directory),
            status=initial_status,
            files=files,
        )

        status = initial_status
        latest = await self._repository.latest_for_thread(thread_id, str(directory))
        if latest is not None and latest.id == snapshot.id:
            status = "reused"
            snapshot = latest
        else:
            await self._repository.save(snapshot)

        rendered = _render_context(snapshot, rendered_parts)
        return CwdContextEvaluation(
            status=status,
            snapshot=snapshot,
            rendered=rendered,
            evidence=_evidence(run_id=run_id, status=status, snapshot=snapshot),
        )

    def _read_files(
        self,
        directory: Path,
    ) -> tuple[list[CwdContextFileSnapshot], dict[str, list[str]]]:
        files: list[CwdContextFileSnapshot] = []
        rendered_parts: dict[str, list[str]] = {}
        seen_normalized: set[str] = set()

        for filename in CONTEXT_FILENAMES:
            path = directory / filename
            snapshot, injected_lines = self._read_one_file(
                filename=filename,
                path=path,
                seen_normalized=seen_normalized,
            )
            files.append(snapshot)
            if injected_lines or snapshot.skipped_reason in {"oversize", "read_error"}:
                rendered_parts[filename] = injected_lines
        return files, rendered_parts

    def _read_one_file(
        self,
        *,
        filename: str,
        path: Path,
        seen_normalized: set[str],
    ) -> tuple[CwdContextFileSnapshot, list[str]]:
        if not path.exists():
            return (
                CwdContextFileSnapshot(
                    filename=filename,
                    path=str(path),
                    exists=False,
                    skipped_reason="missing",
                ),
                [],
            )
        try:
            stat = path.stat()
        except OSError:
            return (
                CwdContextFileSnapshot(
                    filename=filename,
                    path=str(path),
                    exists=True,
                    skipped_reason="stat_error",
                ),
                [],
            )
        if not path.is_file():
            return (
                CwdContextFileSnapshot(
                    filename=filename,
                    path=str(path),
                    exists=True,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    skipped_reason="not_file",
                ),
                [],
            )
        if stat.st_size > self._max_file_bytes:
            return (
                CwdContextFileSnapshot(
                    filename=filename,
                    path=str(path),
                    exists=True,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    skipped_reason="oversize",
                ),
                [],
            )
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8", errors="replace")
        except OSError:
            return (
                CwdContextFileSnapshot(
                    filename=filename,
                    path=str(path),
                    exists=True,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    skipped_reason="read_error",
                ),
                [],
            )

        digest = hashlib.sha256(raw).hexdigest()
        lines = text.splitlines()
        injected: list[str] = []
        deduped = 0
        for line in lines:
            normalized = _normalize_line(line)
            if not normalized:
                injected.append(line)
                continue
            if filename == "CLAUDE.md" and normalized in seen_normalized:
                deduped += 1
                continue
            injected.append(line)
            seen_normalized.add(normalized)

        return (
            CwdContextFileSnapshot(
                filename=filename,
                path=str(path),
                exists=True,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha256=digest,
                included=bool(injected),
                original_lines=len(lines),
                injected_lines=len(injected),
                deduped_lines=deduped,
            ),
            injected,
        )


def _disabled(
    status: str,
    *,
    run_id: UUID,
    working_directory: str | None = None,
) -> CwdContextEvaluation:
    return CwdContextEvaluation(
        status=status,
        snapshot=None,
        rendered="",
        evidence={
            "operation": "cwd_context_evaluated",
            "run_id": str(run_id),
            "snapshot_id": None,
            "status": status,
            "working_directory": working_directory,
            "precedence": PRECEDENCE,
            "conflict_policy": CONFLICT_POLICY,
            "files": [],
        },
    )


def _normalize_line(line: str) -> str:
    return " ".join(line.strip().split())


def _snapshot_id(
    thread_id: UUID,
    directory: Path,
    files: list[CwdContextFileSnapshot],
) -> str:
    hasher = hashlib.sha256()
    hasher.update(str(thread_id).encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(str(directory).encode("utf-8"))
    for file in files:
        hasher.update(b"\0")
        hasher.update(file.model_dump_json(exclude={"path"}).encode("utf-8"))
    return f"cwdctx_{hasher.hexdigest()[:32]}"


def _render_context(
    snapshot: CwdContextSnapshot,
    rendered_parts: dict[str, list[str]],
) -> str:
    sections: list[str] = []
    for file in snapshot.files:
        if file.included:
            lines = rendered_parts.get(file.filename, [])
            if lines:
                sections.append(f"{file.filename}:\n" + "\n".join(lines))
        elif file.skipped_reason == "oversize":
            sections.append(
                f"{file.filename} was skipped because it exceeded "
                f"{MAX_CONTEXT_FILE_BYTES} bytes."
            )
        elif file.skipped_reason == "read_error":
            sections.append(f"{file.filename} could not be read.")

    if not sections:
        return ""

    return "\n\n".join(
        [
            "<awesome_agent_cwd_context>",
            "Source: local working directory context files",
            f"Working directory: {snapshot.working_directory}",
            f"Precedence: {snapshot.precedence}",
            f"Conflict policy: {snapshot.conflict_policy}",
            *sections,
            "</awesome_agent_cwd_context>",
        ]
    )


def _evidence(
    *,
    run_id: UUID,
    status: str,
    snapshot: CwdContextSnapshot,
) -> dict[str, object]:
    return {
        "operation": "cwd_context_evaluated",
        "run_id": str(run_id),
        "snapshot_id": snapshot.id,
        "status": status,
        "working_directory": snapshot.working_directory,
        "precedence": snapshot.precedence,
        "conflict_policy": snapshot.conflict_policy,
        "files": [
            {
                "filename": file.filename,
                "path": file.path,
                "exists": file.exists,
                "size_bytes": file.size_bytes,
                "mtime_ns": file.mtime_ns,
                "sha256": file.sha256,
                "included": file.included,
                "skipped_reason": file.skipped_reason,
                "deduped_lines": file.deduped_lines,
            }
            for file in snapshot.files
        ],
    }
