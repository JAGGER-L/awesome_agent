from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from awesome_agent.core.changes.analysis import ChangeAnalyzer, merge_file_changes
from awesome_agent.core.changes.errors import (
    ChangeBlobCorrupt,
    ChangeConflict,
    ChangeLifecycleError,
    ChangeNotReversible,
    ChangeSetNotFound,
)
from awesome_agent.core.changes.filesystem import (
    BoundWorkspaceNode,
    NodeSnapshot,
    WorkspaceTreeTransaction,
    normalize_workspace_relative,
    snapshot_digest,
    snapshots_match,
)
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
from awesome_agent.core.filesystem import MutationTargetChanged, UnsafeWorkspacePath
from awesome_agent.core.workspace import WorkspaceIdentity


class ChangeOperationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    change_set_id: str
    lifecycle: ChangeLifecycle
    restored_paths: tuple[str, ...]
    unmanaged_effects_restored: bool = False
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class _PreparedRestore:
    change: FileChange
    target: BoundWorkspaceNode
    desired: NodeSnapshot | None
    pending: PendingMutation


def _kind(before: NodeSnapshot | None, after: NodeSnapshot | None) -> FileChangeKind:
    if before is None:
        return FileChangeKind.CREATED
    if after is None:
        return FileChangeKind.DELETED
    return FileChangeKind.UPDATED


class ChangeOperations:
    def __init__(
        self,
        store: ChangeSetStore,
        blobs: ChangeBlobStore,
        workspace: WorkspaceIdentity,
        analyzer: ChangeAnalyzer | None = None,
    ) -> None:
        self._store = store
        self._blobs = blobs
        self._workspace = workspace
        self._analyzer = analyzer or ChangeAnalyzer(store, blobs, workspace)

    async def _get(self, change_set_id: str) -> ChangeSet:
        change_set = await self._store.get(change_set_id)
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
        node_type = (
            change.resolved_before_node_type
            if before
            else change.resolved_after_node_type
        )
        if digest is None:
            return None
        if node_type is None:
            raise ChangeBlobCorrupt(f"Change node type is missing for {change.path}.")
        if node_type is FileNodeType.DIRECTORY:
            return NodeSnapshot(node_type, None, mode)
        if blob is None:
            raise ChangeBlobCorrupt(
                f"Change blob reference is missing for {change.path}."
            )
        try:
            content = self._blobs.get(blob)
        except (KeyError, FileNotFoundError, OSError) as error:
            raise ChangeBlobCorrupt(
                f"Change blob could not be read for {change.path}."
            ) from error
        if hashlib.sha256(content).hexdigest() != digest:
            raise ChangeBlobCorrupt(
                f"Change blob content does not match the record for {change.path}."
            )
        return NodeSnapshot(node_type, content, mode)

    async def diff(self, change_set_id: str) -> str:
        return (await self._analyzer.analyze(change_set_id)).diff

    def _pending(
        self,
        *,
        pending_id: str,
        change_set: ChangeSet,
        change: FileChange,
        current: NodeSnapshot | None,
        desired: NodeSnapshot | None,
    ) -> PendingMutation:
        before_node_type = current.node_type if current is not None else None
        intended_after_node_type = desired.node_type if desired is not None else None
        node_type = intended_after_node_type or before_node_type or change.node_type
        return PendingMutation(
            id=pending_id,
            change_set_id=change_set.id,
            workspace_key=change_set.workspace_key,
            relative_path=normalize_workspace_relative(change.path).as_posix(),
            kind=_kind(current, desired),
            node_type=node_type,
            before_node_type=before_node_type,
            intended_after_node_type=intended_after_node_type,
            before_hash=snapshot_digest(current),
            before_blob=(
                self._blobs.put(current.content)
                if current is not None and current.content is not None
                else None
            ),
            before_mode=current.mode if current is not None else None,
            intended_after_hash=snapshot_digest(desired),
            intended_after_blob=(
                self._blobs.put(desired.content)
                if desired is not None and desired.content is not None
                else None
            ),
            intended_after_mode=desired.mode if desired is not None else None,
            created_at=datetime.now(UTC),
        )

    def _prepare(
        self,
        *,
        tree: WorkspaceTreeTransaction,
        change_set: ChangeSet,
        changes: list[FileChange],
        undo: bool,
    ) -> list[_PreparedRestore]:
        targets: dict[str, BoundWorkspaceNode] = {}
        conflicts: list[str] = []
        for change in changes:
            relative = normalize_workspace_relative(change.path).as_posix()
            target = tree.bind(relative)
            expected = self._snapshot(change, before=not undo)
            targets[relative] = target
            if not snapshots_match(target.snapshot, expected):
                conflicts.append(relative)
        if conflicts:
            raise ChangeConflict(
                "Workspace changed after the recorded operation: "
                + ", ".join(sorted(conflicts))
            )

        action = "undo" if undo else "redo"
        operation_id = uuid4().hex
        ordered = list(reversed(changes)) if undo else changes
        prepared: list[_PreparedRestore] = []
        for index, change in enumerate(ordered):
            relative = normalize_workspace_relative(change.path).as_posix()
            target = targets[relative]
            desired = self._snapshot(change, before=undo)
            prepared.append(
                _PreparedRestore(
                    change=change,
                    target=target,
                    desired=desired,
                    pending=self._pending(
                        pending_id=f"{action}_{operation_id}_{index:04d}",
                        change_set=change_set,
                        change=change,
                        current=target.snapshot,
                        desired=desired,
                    ),
                )
            )
        return prepared

    async def _rollback(
        self,
        tree: WorkspaceTreeTransaction,
        prepared: list[_PreparedRestore],
        applied: list[_PreparedRestore],
    ) -> bool:
        try:
            for item in reversed(applied):
                current = tree.bind(item.pending.relative_path)
                if not snapshots_match(current.snapshot, item.desired):
                    return False
                restored = tree.restore(current, item.target.snapshot)
                if not snapshots_match(restored, item.target.snapshot):
                    return False
            for item in prepared:
                current = tree.bind(item.pending.relative_path)
                if not snapshots_match(current.snapshot, item.target.snapshot):
                    return False
        except (MutationTargetChanged, UnsafeWorkspacePath, OSError):
            return False
        try:
            for item in prepared:
                await self._store.delete_pending(item.pending.id)
        except Exception:
            return False
        return True

    async def _operate(
        self,
        change_set_id: str,
        *,
        undo: bool,
    ) -> ChangeOperationResult:
        change_set = await self._get(change_set_id)
        expected_lifecycle = ChangeLifecycle.APPLIED if undo else ChangeLifecycle.UNDONE
        target_lifecycle = ChangeLifecycle.UNDONE if undo else ChangeLifecycle.APPLIED
        if change_set.lifecycle is not expected_lifecycle:
            raise ChangeLifecycleError(
                f"ChangeSet must be {expected_lifecycle.value} for this operation."
            )
        if change_set.reversibility is ChangeReversibility.NONE:
            raise ChangeNotReversible("ChangeSet contains only unmanaged effects.")

        changes = list(merge_file_changes(change_set.files))
        prepared: list[_PreparedRestore] = []
        applied: list[_PreparedRestore] = []
        committed = False
        try:
            with WorkspaceTreeTransaction(self._workspace) as tree:
                try:
                    prepared = self._prepare(
                        tree=tree,
                        change_set=change_set,
                        changes=changes,
                        undo=undo,
                    )
                    for item in prepared:
                        await self._store.save_pending(item.pending)
                    for item in prepared:
                        actual = tree.restore(item.target, item.desired)
                        if not snapshots_match(actual, item.desired):
                            raise ChangeConflict(
                                f"Could not restore {item.pending.relative_path} "
                                "exactly."
                            )
                        applied.append(item)
                    updated = change_set.model_copy(
                        update={"lifecycle": target_lifecycle}
                    )
                    await self._store.save(updated)
                    committed = True
                    for item in prepared:
                        await self._store.delete_pending(item.pending.id)
                except Exception:
                    if prepared and not committed:
                        try:
                            persisted = await self._store.get(change_set.id)
                        except Exception:
                            persisted = None
                        if (
                            persisted is not None
                            and persisted.lifecycle is target_lifecycle
                        ):
                            committed = True
                        elif (
                            persisted is not None
                            and persisted.lifecycle is expected_lifecycle
                        ):
                            await self._rollback(tree, prepared, applied)
                    raise
        except (MutationTargetChanged, UnsafeWorkspacePath, OSError) as error:
            raise ChangeConflict(
                "Workspace changed while the change operation was in progress."
            ) from error

        warning = None
        if change_set.reversibility is ChangeReversibility.PARTIAL:
            warning = "Unmanaged execute effects were not restored."
        return ChangeOperationResult(
            change_set_id=change_set.id,
            lifecycle=target_lifecycle,
            restored_paths=tuple(item.pending.relative_path for item in prepared),
            unmanaged_effects_restored=False,
            warning=warning,
        )

    async def undo(self, change_set_id: str) -> ChangeOperationResult:
        return await self._operate(change_set_id, undo=True)

    async def redo(self, change_set_id: str) -> ChangeOperationResult:
        return await self._operate(change_set_id, undo=False)
