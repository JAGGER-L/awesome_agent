from __future__ import annotations

import hashlib
import shlex
from pathlib import Path, PureWindowsPath

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.context.tokens import estimate_text
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.policy import resolve_workspace_path
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
    current = workspace.canonical_path
    for part in Path(reference).parts:
        current /= part
        if current.is_symlink():
            raise ExplicitPathError("Explicit paths cannot traverse a symlink.")
    try:
        safe = resolve_workspace_path(workspace, reference, must_exist=True)
    except ExpectedToolFailure as error:
        raise ExplicitPathError(error.message) from error
    if safe.resolved.suffix.casefold() in _NON_TEXT_SUFFIXES:
        raise ExplicitPathError("Explicit paths support text only.")
    if safe.resolved.is_file():
        content, truncated = _file_content(safe.resolved, safe.relative.as_posix())
        kind = "file"
    elif safe.resolved.is_dir():
        content, truncated = _directory_content(safe.resolved, safe.relative.as_posix())
        kind = "directory"
    else:
        raise ExplicitPathError("Explicit path is not a file or directory.")
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


def _file_content(path: Path, relative: str) -> tuple[str, bool]:
    if path.stat().st_size > MAX_EXPLICIT_FILE_BYTES:
        raise ExplicitPathError("Explicit file exceeds the 1 MiB limit.")
    data = path.read_bytes()
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


def _directory_content(path: Path, relative: str) -> tuple[str, bool]:
    children = sorted(path.iterdir(), key=lambda child: child.name.casefold())
    retained = children[:MAX_EXPLICIT_DIRECTORY_ENTRIES]
    entries = [
        f"{child.name}\t{'directory' if child.is_dir() else 'file'}"
        for child in retained
    ]
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
