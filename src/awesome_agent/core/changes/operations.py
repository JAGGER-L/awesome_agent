from __future__ import annotations

import difflib
import hashlib
import os
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from awesome_agent.core.changes.errors import (
    ChangeConflict,
    ChangeLifecycleError,
    ChangeNotReversible,
    ChangeSetNotFound,
)
from awesome_agent.core.changes.journal import NodeSnapshot
from awesome_agent.core.changes.models import (
    ChangeLifecycle,
    ChangeReversibility,
    ChangeSet,
    FileChange,
    FileChangeKind,
    FileNodeType,
)
from awesome_agent.core.changes.ports import (
    ChangeBlobStore,
    ChangeSetStore,
    PendingMutation,
)
from awesome_agent.core.workspace import WorkspaceIdentity

MAX_DIFF_CHARS = 30_000


class ChangeOperationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    change_set_id: str
    lifecycle: ChangeLifecycle
    restored_paths: tuple[str, ...]
    unmanaged_effects_restored: bool = False
    warning: str | None = None


def _capture(path: Path) -> NodeSnapshot | None:
    if not path.exists() and not path.is_symlink():
        return None
    status = path.lstat()
    mode = stat.S_IMODE(status.st_mode)
    if path.is_symlink():
        return NodeSnapshot(FileNodeType.SYMLINK, os.fsencode(os.readlink(path)), mode)
    if path.is_dir():
        return NodeSnapshot(FileNodeType.DIRECTORY, None, mode)
    return NodeSnapshot(FileNodeType.FILE, path.read_bytes(), mode)


def _digest(snapshot: NodeSnapshot | None) -> str | None:
    if snapshot is None:
        return None
    return hashlib.sha256(snapshot.content or b"").hexdigest()


def _matches(snapshot: NodeSnapshot | None, expected: NodeSnapshot | None) -> bool:
    if expected is None:
        return snapshot is None
    return (
        snapshot is not None
        and snapshot.node_type is expected.node_type
        and _digest(snapshot) == _digest(expected)
        and snapshot.mode == expected.mode
    )


def _kind(before: NodeSnapshot | None, after: NodeSnapshot | None) -> FileChangeKind:
    if before is None:
        return FileChangeKind.CREATED
    if after is None:
        return FileChangeKind.DELETED
    return FileChangeKind.UPDATED


def _atomic_write(path: Path, content: bytes, mode: int | None) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        if mode is not None:
            os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _remove(path: Path, snapshot: NodeSnapshot) -> None:
    if snapshot.node_type is FileNodeType.DIRECTORY:
        path.rmdir()
    else:
        path.unlink()


def _write_snapshot(path: Path, snapshot: NodeSnapshot | None) -> None:
    current = _capture(path)
    if snapshot is None:
        if current is not None:
            _remove(path, current)
        return
    if current is not None and current.node_type is not snapshot.node_type:
        _remove(path, current)
    if snapshot.node_type is FileNodeType.DIRECTORY:
        path.mkdir(exist_ok=True)
        if snapshot.mode is not None:
            os.chmod(path, snapshot.mode)
        return
    if snapshot.node_type is FileNodeType.SYMLINK:
        if path.exists() or path.is_symlink():
            path.unlink()
        target = os.fsdecode(snapshot.content or b"")
        target_path = (path.parent / target).resolve(strict=False)
        path.symlink_to(target, target_is_directory=target_path.is_dir())
        return
    _atomic_write(path, snapshot.content or b"", snapshot.mode)


def _merge_changes(changes: list[FileChange]) -> list[FileChange]:
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
                "node_type": (change.node_type if after_exists else existing.node_type),
                "after_hash": change.after_hash,
                "after_blob": change.after_blob,
                "after_mode": change.after_mode,
            }
        )
    return [merged[path] for path in order]


class ChangeOperations:
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
        content = self._blobs.get(blob) if blob is not None else None
        return NodeSnapshot(change.node_type, content, mode)

    def diff(self, change_set_id: str) -> str:
        change_set = self._get(change_set_id)
        parts: list[str] = []
        for change in sorted(
            _merge_changes(change_set.files), key=lambda item: item.path
        ):
            before = self._snapshot(change, before=True)
            after = self._snapshot(change, before=False)
            before_content = before.content if before is not None else b""
            after_content = after.content if after is not None else b""
            if (
                change.node_type is not FileNodeType.FILE
                or b"\x00" in (before_content or b"")
                or b"\x00" in (after_content or b"")
            ):
                before_size = len(before_content or b"")
                after_size = len(after_content or b"")
                parts.append(
                    f"Binary change: {change.path} "
                    f"({before_size} -> {after_size} bytes)\n"
                )
                continue
            try:
                before_text = (before_content or b"").decode("utf-8")
                after_text = (after_content or b"").decode("utf-8")
            except UnicodeDecodeError:
                parts.append(f"Binary change: {change.path}\n")
                continue
            parts.extend(
                difflib.unified_diff(
                    before_text.splitlines(keepends=True),
                    after_text.splitlines(keepends=True),
                    fromfile=f"a/{change.path}",
                    tofile=f"b/{change.path}",
                )
            )
        return "".join(parts)[:MAX_DIFF_CHARS]

    def _preflight(
        self,
        changes: list[FileChange],
        *,
        before: bool,
    ) -> None:
        conflicts: list[str] = []
        for change in changes:
            path = self._workspace.canonical_path / change.path
            expected = self._snapshot(change, before=before)
            if not _matches(_capture(path), expected):
                conflicts.append(change.path)
        if conflicts:
            raise ChangeConflict(
                "Workspace changed after the recorded operation: "
                + ", ".join(sorted(conflicts))
            )

    def _apply_one(
        self,
        *,
        action: str,
        change_set: ChangeSet,
        change: FileChange,
        desired: NodeSnapshot | None,
    ) -> None:
        path = self._workspace.canonical_path / change.path
        current = _capture(path)
        node_type = desired.node_type if desired is not None else change.node_type
        pending = PendingMutation(
            id=f"{action}_{uuid4().hex}",
            change_set_id=change_set.id,
            workspace_key=change_set.workspace_key,
            relative_path=change.path,
            kind=_kind(current, desired),
            node_type=node_type,
            before_hash=_digest(current),
            before_blob=(
                self._blobs.put(current.content)
                if current is not None and current.content is not None
                else None
            ),
            before_mode=current.mode if current is not None else None,
            intended_after_hash=_digest(desired),
            intended_after_blob=(
                self._blobs.put(desired.content)
                if desired is not None and desired.content is not None
                else None
            ),
            intended_after_mode=desired.mode if desired is not None else None,
            created_at=datetime.now(UTC),
        )
        self._store.save_pending(pending)
        _write_snapshot(path, desired)
        if not _matches(_capture(path), desired):
            raise ChangeConflict(f"Could not restore {change.path} exactly.")
        self._store.delete_pending(pending.id)

    def _operate(
        self,
        change_set_id: str,
        *,
        undo: bool,
    ) -> ChangeOperationResult:
        change_set = self._get(change_set_id)
        expected_lifecycle = ChangeLifecycle.APPLIED if undo else ChangeLifecycle.UNDONE
        target_lifecycle = ChangeLifecycle.UNDONE if undo else ChangeLifecycle.APPLIED
        if change_set.lifecycle is not expected_lifecycle:
            raise ChangeLifecycleError(
                f"ChangeSet must be {expected_lifecycle.value} for this operation."
            )
        if change_set.reversibility is ChangeReversibility.NONE:
            raise ChangeNotReversible("ChangeSet contains only unmanaged effects.")

        changes = _merge_changes(change_set.files)
        self._preflight(changes, before=not undo)
        ordered = list(reversed(changes)) if undo else changes
        for change in ordered:
            self._apply_one(
                action="undo" if undo else "redo",
                change_set=change_set,
                change=change,
                desired=self._snapshot(change, before=undo),
            )
        updated = change_set.model_copy(update={"lifecycle": target_lifecycle})
        self._store.save(updated)
        warning = None
        if change_set.reversibility is ChangeReversibility.PARTIAL:
            warning = "Unmanaged execute effects were not restored."
        return ChangeOperationResult(
            change_set_id=change_set.id,
            lifecycle=target_lifecycle,
            restored_paths=tuple(change.path for change in ordered),
            unmanaged_effects_restored=False,
            warning=warning,
        )

    def undo(self, change_set_id: str) -> ChangeOperationResult:
        return self._operate(change_set_id, undo=True)

    def redo(self, change_set_id: str) -> ChangeOperationResult:
        return self._operate(change_set_id, undo=False)
