from __future__ import annotations

import hashlib
import shlex
from pathlib import Path, PureWindowsPath

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.context.tokens import estimate_text
from awesome_agent.core.filesystem import (
    MutationTargetChanged,
    PinnedWorkspacePath,
    SafeDirectoryEntry,
    UnsafeWorkspacePath,
    WorkspaceFileTooLarge,
    list_directory_entries,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.policy import (
    is_sensitive_workspace_path,
    resolve_workspace_path,
)
from awesome_agent.core.workspace import WorkspaceIdentity

MAX_EXPLICIT_PATHS = 32
MAX_EXPLICIT_FILE_BYTES = 1024 * 1024
MAX_EXPLICIT_FILE_LINES = 500
MAX_EXPLICIT_DIRECTORY_ENTRIES = 200
_ESCAPED_AT = "__AWESOME_ESCAPED_AT__"
_GLOB_CHARACTERS = frozenset("*?[]{}")
_NON_TEXT_SUFFIXES = frozenset(
    {
        ".avif",
        ".bmp",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
        ".webp",
    }
)


class ExplicitPathError(ValueError):
    pass


class ParsedExplicitPaths(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    references: tuple[str, ...]


class ExplicitPathSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference: str = Field(min_length=1, max_length=2_000)
    relative_path: str = Field(min_length=1, max_length=2_000)
    kind: str = Field(pattern=r"^(file|directory)$")
    content: str = Field(max_length=1_000_000)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    estimated_tokens: int = Field(ge=0)
    truncated: bool


def parse_explicit_paths(text: str) -> ParsedExplicitPaths:
    protected = text.replace(r"\@", _ESCAPED_AT)
    try:
        tokens = shlex.split(protected, posix=True)
    except ValueError as error:
        raise ExplicitPathError("Explicit path quoting is invalid.") from error
    references: list[str] = []
    natural: list[str] = []
    for token in tokens:
        restored = token.replace(_ESCAPED_AT, "@")
        if token.startswith("@"):
            reference = restored[1:]
            if not reference:
                natural.append(restored)
                continue
            if reference not in references:
                references.append(reference)
        else:
            natural.append(restored)
    if len(references) > MAX_EXPLICIT_PATHS:
        raise ExplicitPathError("At most 32 explicit paths are allowed.")
    return ParsedExplicitPaths(text=" ".join(natural), references=tuple(references))


def snapshot_explicit_paths(
    workspace: WorkspaceIdentity,
    references: tuple[str, ...],
    *,
    token_budget: int,
) -> tuple[ExplicitPathSnapshot, ...]:
    if len(references) > MAX_EXPLICIT_PATHS:
        raise ExplicitPathError("At most 32 explicit paths are allowed.")
    if token_budget < 0:
        raise ValueError("token_budget cannot be negative")
    snapshots: list[ExplicitPathSnapshot] = []
    remaining = token_budget
    for reference in references:
        snapshot = _snapshot_one(workspace, reference)
        if remaining <= 0:
            break
        bounded = _fit_snapshot(snapshot, remaining)
        if bounded is None:
            break
        snapshots.append(bounded)
        remaining -= bounded.estimated_tokens
    return tuple(snapshots)


def _snapshot_one(
    workspace: WorkspaceIdentity,
    reference: str,
) -> ExplicitPathSnapshot:
    _validate_literal_reference(reference)
    try:
        safe = resolve_workspace_path(workspace, reference, must_exist=True)
    except ExpectedToolFailure as error:
        if "link" in error.message.casefold() or "reparse" in error.message.casefold():
            raise ExplicitPathError(
                "Explicit paths cannot traverse a symlink or reparse point."
            ) from error
        raise ExplicitPathError(error.message) from error
    if safe.relative.suffix.casefold() in _NON_TEXT_SUFFIXES:
        raise ExplicitPathError("Explicit paths support text only.")
    try:
        with PinnedWorkspacePath(
            safe.workspace,
            safe.workspace_root_identity,
            safe.relative,
            safe.target_existed,
            safe.target_identity,
        ) as reader:
            kind = reader.kind()
            if kind == "file":
                opened = reader.read_regular(max_bytes=MAX_EXPLICIT_FILE_BYTES)
                content, truncated = _file_content(
                    opened.data,
                    safe.relative.as_posix(),
                )
            else:
                directory = reader.open_directory()
                children = tuple(
                    entry
                    for entry in list_directory_entries(directory)
                    if not is_sensitive_workspace_path(safe.relative / entry.name)
                )
                content, truncated = _directory_content(
                    children,
                    safe.relative.as_posix(),
                )
    except WorkspaceFileTooLarge as error:
        raise ExplicitPathError("Explicit file exceeds the 1 MiB limit.") from error
    except MutationTargetChanged as error:
        raise ExplicitPathError(
            "Explicit path changed while its content was being captured."
        ) from error
    except UnsafeWorkspacePath as error:
        raise ExplicitPathError(
            "Explicit paths cannot traverse a symlink, reparse point, or hard link."
        ) from error
    except FileNotFoundError as error:
        raise ExplicitPathError("Explicit path was not found.") from error
    except OSError as error:
        raise ExplicitPathError("Explicit path could not be read safely.") from error
    return _snapshot(
        reference=reference,
        relative_path=safe.relative.as_posix(),
        kind=kind,
        content=content,
        truncated=truncated,
    )


def _validate_literal_reference(reference: str) -> None:
    lowered = reference.casefold()
    windows = PureWindowsPath(reference)
    path = Path(reference)
    if (
        not reference
        or lowered.startswith(("http://", "https://", "file://"))
        or path.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in path.parts
        or any(character in reference for character in _GLOB_CHARACTERS)
    ):
        raise ExplicitPathError(
            "Explicit path must be a literal workspace-relative path."
        )


def _file_content(data: bytes, relative: str) -> tuple[str, bool]:
    if b"\x00" in data:
        raise ExplicitPathError("Binary files cannot be explicit context.")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ExplicitPathError("Explicit file is not UTF-8 text.") from error
    lines = text.splitlines()
    retained = lines[:MAX_EXPLICIT_FILE_LINES]
    rendered = [f"[File: {relative}]", *retained]
    return "\n".join(rendered), len(lines) > len(retained)


def _directory_content(
    children: tuple[SafeDirectoryEntry, ...],
    relative: str,
) -> tuple[str, bool]:
    retained = children[:MAX_EXPLICIT_DIRECTORY_ENTRIES]
    entries = [f"{child.name}\t{child.kind}" for child in retained]
    return "\n".join([f"[Directory: {relative}]", *entries]), len(children) > len(
        retained
    )


def _fit_snapshot(
    snapshot: ExplicitPathSnapshot,
    budget: int,
) -> ExplicitPathSnapshot | None:
    if snapshot.estimated_tokens <= budget:
        return snapshot
    lines = snapshot.content.splitlines()
    retained: list[str] = []
    for line in lines:
        candidate = "\n".join([*retained, line])
        if estimate_text(candidate) > budget:
            break
        retained.append(line)
    if not retained:
        return None
    return _snapshot(
        reference=snapshot.reference,
        relative_path=snapshot.relative_path,
        kind=snapshot.kind,
        content="\n".join(retained),
        truncated=True,
    )


def _snapshot(
    *,
    reference: str,
    relative_path: str,
    kind: str,
    content: str,
    truncated: bool,
) -> ExplicitPathSnapshot:
    return ExplicitPathSnapshot(
        reference=reference,
        relative_path=relative_path,
        kind=kind,
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        estimated_tokens=estimate_text(content),
        truncated=truncated,
    )
