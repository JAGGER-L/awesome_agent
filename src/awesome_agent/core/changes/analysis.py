from __future__ import annotations

import difflib
import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.core.changes.errors import (
    ChangeBlobCorrupt,
    ChangeLifecycleError,
    ChangeSetNotFound,
)
from awesome_agent.core.changes.journal import NodeSnapshot
from awesome_agent.core.changes.models import (
    ChangeSet,
    FileChange,
    FileChangeKind,
    FileNodeType,
)
from awesome_agent.core.changes.ports import ChangeBlobStore, ChangeSetStore
from awesome_agent.core.workspace import WorkspaceIdentity

MAX_DIFF_CHARS = 30_000


class TextFileChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["text_file"] = "text_file"
    path: str = Field(min_length=1, max_length=1_000)
    change_kind: FileChangeKind
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)


class BinaryFileChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["binary_file"] = "binary_file"
    path: str = Field(min_length=1, max_length=1_000)
    change_kind: FileChangeKind
    before_bytes: int = Field(ge=0)
    after_bytes: int = Field(ge=0)


class DirectoryChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["directory"] = "directory"
    path: str = Field(min_length=1, max_length=1_000)
    change_kind: FileChangeKind


class SymlinkChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["symlink"] = "symlink"
    path: str = Field(min_length=1, max_length=1_000)
    change_kind: FileChangeKind


ChangeDelta = Annotated[
    TextFileChange | BinaryFileChange | DirectoryChange | SymlinkChange,
    Field(discriminator="kind"),
]


class ChangeAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    diff: str = Field(default="", max_length=MAX_DIFF_CHARS)
    changes: tuple[ChangeDelta, ...] = Field(default=(), max_length=1_000)


def merge_file_changes(changes: list[FileChange]) -> tuple[FileChange, ...]:
    merged: dict[str, FileChange] = {}
    order: list[str] = []
    for change in changes:
        existing = merged.get(change.path)
        if existing is None:
            merged[change.path] = change
            order.append(change.path)
            continue
        before_exists = existing.before_hash is not None
        after_exists = change.after_hash is not None
        if not before_exists and not after_exists:
            merged.pop(change.path)
            order.remove(change.path)
            continue
        if not before_exists:
            kind = FileChangeKind.CREATED
        elif not after_exists:
            kind = FileChangeKind.DELETED
        else:
            kind = FileChangeKind.UPDATED
        merged[change.path] = existing.model_copy(
            update={
                "kind": kind,
                "node_type": change.node_type if after_exists else existing.node_type,
                "after_hash": change.after_hash,
                "after_blob": change.after_blob,
                "after_mode": change.after_mode,
            }
        )
    return tuple(merged[path] for path in order)


class ChangeAnalyzer:
    def __init__(
        self,
        store: ChangeSetStore,
        blobs: ChangeBlobStore,
        workspace: WorkspaceIdentity,
    ) -> None:
        self._store = store
        self._blobs = blobs
        self._workspace = workspace

    def _get(self, change_set_id: str) -> ChangeSet:
        change_set = self._store.get(change_set_id)
        if change_set is None:
            raise ChangeSetNotFound(change_set_id)
        if change_set.workspace_key != self._workspace.key:
            raise ChangeLifecycleError("ChangeSet belongs to another workspace.")
        return change_set

    def _snapshot(
        self,
        change: FileChange,
        *,
        before: bool,
    ) -> NodeSnapshot | None:
        digest = change.before_hash if before else change.after_hash
        blob = change.before_blob if before else change.after_blob
        mode = change.before_mode if before else change.after_mode
        if digest is None:
            return None
        if change.node_type is FileNodeType.DIRECTORY:
            return NodeSnapshot(change.node_type, None, mode)
        if blob is None:
            raise ChangeBlobCorrupt(
                f"Change blob reference is missing for {change.path}."
            )
        content = self._blobs.get(blob)
        if hashlib.sha256(content).hexdigest() != digest:
            raise ChangeBlobCorrupt(
                "Change blob content does not match the recorded hash for "
                f"{change.path}."
            )
        return NodeSnapshot(change.node_type, content, mode)

    def analyze(self, change_set_id: str) -> ChangeAnalysis:
        change_set = self._get(change_set_id)
        diff_parts: list[str] = []
        deltas: list[ChangeDelta] = []
        for change in sorted(
            merge_file_changes(change_set.files),
            key=lambda item: item.path,
        ):
            before = self._snapshot(change, before=True)
            after = self._snapshot(change, before=False)
            before_content = (
                before.content
                if before is not None and before.content is not None
                else b""
            )
            after_content = (
                after.content
                if after is not None and after.content is not None
                else b""
            )

            if change.node_type is FileNodeType.DIRECTORY:
                deltas.append(
                    DirectoryChange(path=change.path, change_kind=change.kind)
                )
                diff_parts.append(
                    f"Directory change: {change.path} ({change.kind.value})\n"
                )
                continue
            if change.node_type is FileNodeType.SYMLINK:
                deltas.append(SymlinkChange(path=change.path, change_kind=change.kind))
                diff_parts.append(
                    f"Symlink change: {change.path} ({change.kind.value})\n"
                )
                continue

            if b"\x00" in before_content or b"\x00" in after_content:
                self._append_binary(change, before_content, after_content, deltas)
                diff_parts.append(
                    _binary_diff(change.path, before_content, after_content)
                )
                continue
            try:
                before_text = before_content.decode("utf-8")
                after_text = after_content.decode("utf-8")
            except UnicodeDecodeError:
                self._append_binary(change, before_content, after_content, deltas)
                diff_parts.append(
                    _binary_diff(change.path, before_content, after_content)
                )
                continue

            before_lines = before_text.splitlines(keepends=True)
            after_lines = after_text.splitlines(keepends=True)
            additions, deletions = _line_counts(before_lines, after_lines)
            deltas.append(
                TextFileChange(
                    path=change.path,
                    change_kind=change.kind,
                    additions=additions,
                    deletions=deletions,
                )
            )
            diff_parts.extend(
                difflib.unified_diff(
                    before_lines,
                    after_lines,
                    fromfile=f"a/{change.path}",
                    tofile=f"b/{change.path}",
                )
            )
        return ChangeAnalysis(
            diff="".join(diff_parts)[:MAX_DIFF_CHARS],
            changes=tuple(deltas),
        )

    @staticmethod
    def _append_binary(
        change: FileChange,
        before: bytes,
        after: bytes,
        deltas: list[ChangeDelta],
    ) -> None:
        deltas.append(
            BinaryFileChange(
                path=change.path,
                change_kind=change.kind,
                before_bytes=len(before),
                after_bytes=len(after),
            )
        )


def _line_counts(before: list[str], after: list[str]) -> tuple[int, int]:
    additions = 0
    deletions = 0
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            deletions += before_end - before_start
        if tag in {"replace", "insert"}:
            additions += after_end - after_start
    return additions, deletions


def _binary_diff(path: str, before: bytes, after: bytes) -> str:
    return f"Binary change: {path} ({len(before)} -> {len(after)} bytes)\n"
