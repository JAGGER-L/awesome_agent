from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from awesome_agent.core.changes.errors import (
    ChangeCapacityExceeded,
    ChangeLifecycleError,
    ChangeSetNotFound,
    PendingMutationConflict,
)
from awesome_agent.core.changes.models import (
    ChangeLifecycle,
    ChangeReversibility,
    ChangeSet,
    ExecuteObservation,
    FileChange,
    FileChangeKind,
    FileNodeType,
)
from awesome_agent.core.changes.ports import (
    ChangeBlobStore,
    ChangeSetStore,
    PendingMutation,
)
from awesome_agent.core.contracts import new_identifier
from awesome_agent.core.workspace import WorkspaceIdentity

MAX_CHANGESET_FILES = 1_000
MAX_CHANGESET_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class NodeSnapshot:
    node_type: FileNodeType
    content: bytes | None
    mode: int | None


def _capture_node(path: Path) -> NodeSnapshot | None:
    if not path.exists() and not path.is_symlink():
        return None
    status = path.lstat()
    mode = stat.S_IMODE(status.st_mode)
    if path.is_symlink():
        return NodeSnapshot(
            FileNodeType.SYMLINK,
            os.fsencode(os.readlink(path)),
            mode,
        )
    if path.is_dir():
        return NodeSnapshot(FileNodeType.DIRECTORY, None, mode)
    return NodeSnapshot(FileNodeType.FILE, path.read_bytes(), mode)


def _snapshot_hash(snapshot: NodeSnapshot | None) -> str | None:
    if snapshot is None:
        return None
    return hashlib.sha256(snapshot.content or b"").hexdigest()


def _matches(
    snapshot: NodeSnapshot | None,
    *,
    digest: str | None,
    mode: int | None,
    node_type: FileNodeType,
) -> bool:
    if digest is None:
        return snapshot is None
    return (
        snapshot is not None
        and snapshot.node_type is node_type
        and _snapshot_hash(snapshot) == digest
        and (mode is None or snapshot.mode == mode)
    )


class ChangeJournal:
    def __init__(
        self,
        store: ChangeSetStore,
        blobs: ChangeBlobStore,
        workspace: WorkspaceIdentity,
    ) -> None:
        self._store = store
        self._blobs = blobs
        self._workspace = workspace

    def begin(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        workspace: WorkspaceIdentity,
    ) -> ChangeSet:
        if (
            workspace.key != self._workspace.key
            or workspace.canonical_path != self._workspace.canonical_path
        ):
            raise ChangeLifecycleError("Journal is bound to a different workspace.")
        change_set = ChangeSet(
            id=new_identifier("change"),
            session_id=session_id,
            turn_id=turn_id,
            workspace_key=workspace.key,
            lifecycle=ChangeLifecycle.OPEN,
            reversibility=ChangeReversibility.FULL,
            created_at=datetime.now(UTC),
        )
        self._store.save(change_set)
        return change_set

    def _open(self, change_set_id: str) -> ChangeSet:
        change_set = self._store.get(change_set_id)
        if change_set is None:
            raise ChangeSetNotFound(change_set_id)
        if change_set.workspace_key != self._workspace.key:
            raise ChangeLifecycleError("ChangeSet belongs to another workspace.")
        if change_set.lifecycle is not ChangeLifecycle.OPEN:
            raise ChangeLifecycleError("ChangeSet is not open.")
        return change_set

    def _path(self, path: Path) -> tuple[Path, str]:
        candidate = (
            path if path.is_absolute() else self._workspace.canonical_path / path
        )
        parent = candidate.parent.resolve(strict=True)
        normalized = parent / candidate.name
        if not normalized.is_relative_to(self._workspace.canonical_path):
            raise ChangeLifecycleError("Mutation path escapes the workspace.")
        return normalized, normalized.relative_to(
            self._workspace.canonical_path
        ).as_posix()

    def _stored_bytes(self, change_set: ChangeSet) -> int:
        digests = {
            digest
            for change in change_set.files
            for digest in (change.before_blob, change.after_blob)
            if digest is not None
        }
        return sum(len(self._blobs.get(digest)) for digest in digests)

    def _preflight(
        self,
        change_set: ChangeSet,
        before: NodeSnapshot | None,
        intended_after: NodeSnapshot | None,
    ) -> None:
        if len(change_set.files) + 1 > MAX_CHANGESET_FILES:
            raise ChangeCapacityExceeded("ChangeSet file limit exceeded.")
        additions = {
            snapshot.content
            for snapshot in (before, intended_after)
            if snapshot is not None and snapshot.content is not None
        }
        if (
            self._stored_bytes(change_set) + sum(map(len, additions))
            > MAX_CHANGESET_BYTES
        ):
            raise ChangeCapacityExceeded("ChangeSet byte limit exceeded.")

    def preflight_batch(
        self,
        *,
        change_set_id: str,
        additional_nodes: int,
        additional_bytes: int,
    ) -> None:
        change_set = self._open(change_set_id)
        if additional_nodes < 0 or additional_bytes < 0:
            raise ValueError("Capacity additions cannot be negative.")
        if len(change_set.files) + additional_nodes > MAX_CHANGESET_FILES:
            raise ChangeCapacityExceeded("ChangeSet file limit exceeded.")
        if self._stored_bytes(change_set) + additional_bytes > MAX_CHANGESET_BYTES:
            raise ChangeCapacityExceeded("ChangeSet byte limit exceeded.")

    def apply_file_mutation(
        self,
        *,
        change_set_id: str,
        path: Path,
        kind: FileChangeKind,
        intended_after: NodeSnapshot | None,
        mutate: Callable[[], None],
    ) -> FileChange:
        change_set = self._open(change_set_id)
        target, relative = self._path(path)
        before = _capture_node(target)
        if before is None and intended_after is None:
            raise ChangeLifecycleError("Mutation has no before or after state.")
        self._preflight(change_set, before, intended_after)

        before_blob = (
            self._blobs.put(before.content)
            if before is not None and before.content is not None
            else None
        )
        intended_after_blob = (
            self._blobs.put(intended_after.content)
            if intended_after is not None and intended_after.content is not None
            else None
        )
        if intended_after is not None:
            node_type = intended_after.node_type
        else:
            assert before is not None
            node_type = before.node_type
        pending = PendingMutation(
            id=new_identifier("operation"),
            change_set_id=change_set.id,
            workspace_key=change_set.workspace_key,
            relative_path=relative,
            kind=kind,
            node_type=node_type,
            before_hash=_snapshot_hash(before),
            before_blob=before_blob,
            before_mode=before.mode if before is not None else None,
            intended_after_hash=_snapshot_hash(intended_after),
            intended_after_blob=intended_after_blob,
            intended_after_mode=(
                intended_after.mode if intended_after is not None else None
            ),
            created_at=datetime.now(UTC),
        )
        self._store.save_pending(pending)
        mutate()
        actual_after = _capture_node(target)
        if not _matches(
            actual_after,
            digest=pending.intended_after_hash,
            mode=pending.intended_after_mode,
            node_type=pending.node_type,
        ):
            raise PendingMutationConflict(
                f"Mutation result for {relative} did not match its intended state."
            )

        change = self._file_change(pending, actual_after)
        updated = change_set.model_copy(update={"files": [*change_set.files, change]})
        self._store.save(updated)
        self._store.delete_pending(pending.id)
        return change

    def _file_change(
        self,
        pending: PendingMutation,
        actual_after: NodeSnapshot | None,
    ) -> FileChange:
        after_blob = (
            self._blobs.put(actual_after.content)
            if actual_after is not None and actual_after.content is not None
            else None
        )
        return FileChange(
            path=pending.relative_path,
            kind=pending.kind,
            node_type=pending.node_type,
            before_hash=pending.before_hash,
            after_hash=_snapshot_hash(actual_after),
            before_blob=pending.before_blob,
            after_blob=after_blob,
            before_mode=pending.before_mode,
            after_mode=actual_after.mode if actual_after is not None else None,
        )

    def record_execute(
        self,
        *,
        change_set_id: str,
        command: str,
        observed_paths: list[str],
    ) -> ChangeSet:
        change_set = self._open(change_set_id)
        observation = ExecuteObservation(
            command=command,
            observed_paths=observed_paths,
        )
        updated = change_set.model_copy(
            update={"execute": [*change_set.execute, observation]}
        )
        self._store.save(updated)
        return updated

    def seal(self, change_set_id: str) -> ChangeSet:
        change_set = self._open(change_set_id)
        if change_set.files and change_set.execute:
            reversibility = ChangeReversibility.PARTIAL
        elif change_set.execute:
            reversibility = ChangeReversibility.NONE
        else:
            reversibility = ChangeReversibility.FULL
        sealed = change_set.model_copy(
            update={
                "lifecycle": ChangeLifecycle.APPLIED,
                "reversibility": reversibility,
                "sealed_at": datetime.now(UTC),
            }
        )
        self._store.save(sealed)
        return sealed

    def reconcile_pending(self) -> None:
        for pending in self._store.list_pending():
            if pending.workspace_key != self._workspace.key:
                continue
            change_set = self._open(pending.change_set_id)
            target = self._workspace.canonical_path / pending.relative_path
            current = _capture_node(target)
            if pending.id.startswith(("undo_", "redo_")):
                if _matches(
                    current,
                    digest=pending.before_hash,
                    mode=pending.before_mode,
                    node_type=pending.node_type,
                ):
                    self._store.delete_pending(pending.id)
                    continue
                raise PendingMutationConflict(
                    f"Interrupted change operation for {pending.relative_path}."
                )
            if _matches(
                current,
                digest=pending.before_hash,
                mode=pending.before_mode,
                node_type=pending.node_type,
            ):
                self._store.delete_pending(pending.id)
                continue
            if _matches(
                current,
                digest=pending.intended_after_hash,
                mode=pending.intended_after_mode,
                node_type=pending.node_type,
            ):
                change = self._file_change(pending, current)
                if change not in change_set.files:
                    self._store.save(
                        change_set.model_copy(
                            update={"files": [*change_set.files, change]}
                        )
                    )
                self._store.delete_pending(pending.id)
                continue
            raise PendingMutationConflict(
                f"Pending mutation for {pending.relative_path} conflicts."
            )
