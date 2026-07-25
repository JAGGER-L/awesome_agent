from __future__ import annotations

from datetime import UTC, datetime

from awesome_agent.core.changes.errors import (
    ChangeBlobCorrupt,
    ChangeCapacityExceeded,
    ChangeLifecycleError,
    ChangeSetNotFound,
    PendingMutationConflict,
)
from awesome_agent.core.changes.filesystem import (
    MAX_CHANGESET_BYTES as _MAX_CHANGESET_BYTES,
)
from awesome_agent.core.changes.filesystem import (
    BoundFileMutation,
    BoundWorkspaceNode,
    NodeSnapshot,
    WorkspaceTreeTransaction,
    normalize_workspace_relative,
    snapshot_digest,
    snapshot_matches_record,
    snapshots_match,
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
from awesome_agent.core.filesystem import (
    MutationTargetChanged,
    UnsafeWorkspacePath,
)
from awesome_agent.core.workspace import WorkspaceIdentity

MAX_CHANGESET_FILES = 1_000
MAX_CHANGESET_BYTES = _MAX_CHANGESET_BYTES


def _snapshot_hash(snapshot: NodeSnapshot | None) -> str | None:
    return snapshot_digest(snapshot)


def _matches(
    snapshot: NodeSnapshot | None,
    *,
    digest: str | None,
    mode: int | None,
    node_type: FileNodeType,
) -> bool:
    return snapshot_matches_record(
        snapshot,
        digest=digest,
        mode=mode,
        node_type=node_type,
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
            or workspace.root_identity != self._workspace.root_identity
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
        change_set = self._owned(change_set_id)
        if change_set.lifecycle is not ChangeLifecycle.OPEN:
            raise ChangeLifecycleError("ChangeSet is not open.")
        return change_set

    def _owned(self, change_set_id: str) -> ChangeSet:
        change_set = self._store.get(change_set_id)
        if change_set is None:
            raise ChangeSetNotFound(change_set_id)
        if change_set.workspace_key != self._workspace.key:
            raise ChangeLifecycleError("ChangeSet belongs to another workspace.")
        return change_set

    @staticmethod
    def _relative_path(relative_path: str) -> str:
        return normalize_workspace_relative(relative_path).as_posix()

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

    def _ensure_no_pending(self, change_set_id: str) -> None:
        if any(
            pending.change_set_id == change_set_id
            for pending in self._store.list_pending()
        ):
            raise PendingMutationConflict(
                "ChangeSet has an unresolved pending mutation."
            )

    def preflight_batch(
        self,
        *,
        change_set_id: str,
        additional_nodes: int,
        additional_bytes: int,
    ) -> None:
        change_set = self._open(change_set_id)
        self._ensure_no_pending(change_set_id)
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
        kind: FileChangeKind,
        intended_after: NodeSnapshot | None,
        target: BoundFileMutation,
    ) -> FileChange:
        change_set = self._open(change_set_id)
        self._ensure_no_pending(change_set_id)
        relative = self._relative_path(target.relative_path)
        before = target.before
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
        before_node_type = before.node_type if before is not None else None
        intended_after_node_type = (
            intended_after.node_type if intended_after is not None else None
        )
        node_type = intended_after_node_type or before_node_type
        assert node_type is not None
        pending = PendingMutation(
            id=new_identifier("operation"),
            change_set_id=change_set.id,
            workspace_key=change_set.workspace_key,
            relative_path=relative,
            kind=kind,
            node_type=node_type,
            before_node_type=before_node_type,
            intended_after_node_type=intended_after_node_type,
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
        try:
            target.mutate()
        except MutationTargetChanged:
            self._store.delete_pending(pending.id)
            raise
        actual_after = target.capture_after()
        if not _matches(
            actual_after,
            digest=pending.intended_after_hash,
            mode=pending.intended_after_mode,
            node_type=(pending.resolved_intended_after_node_type or pending.node_type),
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
        before_node_type = pending.resolved_before_node_type
        after_node_type = actual_after.node_type if actual_after is not None else None
        node_type = after_node_type or before_node_type or pending.node_type
        return FileChange(
            mutation_id=pending.id,
            path=pending.relative_path,
            kind=pending.kind,
            node_type=node_type,
            before_node_type=before_node_type,
            after_node_type=after_node_type,
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
        self._ensure_no_pending(change_set_id)
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
        if any(
            pending.change_set_id == change_set_id
            for pending in self._store.list_pending()
        ):
            raise PendingMutationConflict(
                "ChangeSet has unresolved pending mutations and cannot be sealed."
            )
        sealed = change_set.model_copy(
            update={
                "lifecycle": ChangeLifecycle.APPLIED,
                "reversibility": self._reversibility(change_set),
                "sealed_at": datetime.now(UTC),
            }
        )
        self._store.save(sealed)
        return sealed

    @staticmethod
    def _reversibility(change_set: ChangeSet) -> ChangeReversibility:
        if change_set.files and change_set.execute:
            return ChangeReversibility.PARTIAL
        if change_set.execute:
            return ChangeReversibility.NONE
        return ChangeReversibility.FULL

    def _pending_snapshot(
        self,
        pending: PendingMutation,
        *,
        before: bool,
    ) -> NodeSnapshot | None:
        digest = pending.before_hash if before else pending.intended_after_hash
        blob = pending.before_blob if before else pending.intended_after_blob
        mode = pending.before_mode if before else pending.intended_after_mode
        node_type = (
            pending.resolved_before_node_type
            if before
            else pending.resolved_intended_after_node_type
        )
        if digest is None:
            return None
        if node_type is None:
            raise PendingMutationConflict(
                "Pending mutation node type is incomplete and was preserved."
            )
        if node_type is FileNodeType.DIRECTORY:
            return NodeSnapshot(node_type, None, mode)
        if blob is None:
            raise PendingMutationConflict(
                "Pending mutation data is incomplete and was preserved."
            )
        try:
            content = self._blobs.get(blob)
        except (ChangeBlobCorrupt, KeyError, FileNotFoundError, OSError) as error:
            raise PendingMutationConflict(
                "Pending mutation data could not be read and was preserved."
            ) from error
        snapshot = NodeSnapshot(node_type, content, mode)
        if snapshot_digest(snapshot) != digest:
            raise PendingMutationConflict(
                "Pending mutation data is corrupt and was preserved."
            )
        return snapshot

    @staticmethod
    def _change_matches_pending(
        change: FileChange,
        pending: PendingMutation,
    ) -> bool:
        return (
            change.path == pending.relative_path
            and change.kind is pending.kind
            and change.resolved_before_node_type is pending.resolved_before_node_type
            and change.resolved_after_node_type
            is pending.resolved_intended_after_node_type
            and change.before_hash == pending.before_hash
            and change.before_blob == pending.before_blob
            and (
                pending.before_mode is None or change.before_mode == pending.before_mode
            )
            and change.after_hash == pending.intended_after_hash
            and change.after_blob == pending.intended_after_blob
            and (
                pending.intended_after_mode is None
                or change.after_mode == pending.intended_after_mode
            )
        )

    @staticmethod
    def _operation_action(pending: PendingMutation) -> str | None:
        if pending.id.startswith("undo_"):
            return "undo"
        if pending.id.startswith("redo_"):
            return "redo"
        return None

    def _recovery_relative(self, relative_path: str) -> str:
        try:
            return self._relative_path(relative_path)
        except ChangeLifecycleError as error:
            raise PendingMutationConflict(
                "Pending mutation path is outside the workspace boundary."
            ) from error

    def _reconcile_mutation(
        self,
        tree: WorkspaceTreeTransaction,
        pending: PendingMutation,
    ) -> None:
        try:
            change_set = self._owned(pending.change_set_id)
        except (ChangeLifecycleError, ChangeSetNotFound) as error:
            raise PendingMutationConflict(
                "Pending mutation lifecycle is inconsistent."
            ) from error
        if change_set.lifecycle not in {
            ChangeLifecycle.OPEN,
            ChangeLifecycle.APPLIED,
        }:
            raise PendingMutationConflict("Pending mutation lifecycle is inconsistent.")
        target = tree.bind(self._recovery_relative(pending.relative_path))
        current = tree.capture(target)
        before_node_type = pending.resolved_before_node_type or pending.node_type
        intended_after_node_type = (
            pending.resolved_intended_after_node_type or pending.node_type
        )
        committed = [
            change for change in change_set.files if change.mutation_id == pending.id
        ]
        if committed:
            if len(committed) != 1 or not self._change_matches_pending(
                committed[0],
                pending,
            ):
                raise PendingMutationConflict(
                    "Pending state conflicts with a committed mutation."
                )
            committed_change = committed[0]
            if _matches(
                current,
                digest=committed_change.after_hash,
                mode=committed_change.after_mode,
                node_type=(
                    committed_change.resolved_after_node_type
                    or committed_change.node_type
                ),
            ):
                self._store.delete_pending(pending.id)
                return
            raise PendingMutationConflict(
                "Pending state conflicts with a committed mutation."
            )

        if any(
            change.mutation_id is None and self._change_matches_pending(change, pending)
            for change in change_set.files
        ):
            raise PendingMutationConflict(
                "Pending state has an ambiguous legacy mutation identity."
            )

        if _matches(
            current,
            digest=pending.before_hash,
            mode=pending.before_mode,
            node_type=before_node_type,
        ):
            self._store.delete_pending(pending.id)
            return
        if _matches(
            current,
            digest=pending.intended_after_hash,
            mode=pending.intended_after_mode,
            node_type=intended_after_node_type,
        ):
            before = self._pending_snapshot(pending, before=True)
            intended_after = self._pending_snapshot(pending, before=False)
            self._preflight(change_set, before, intended_after)
            change = self._file_change(pending, current)
            updated = change_set.model_copy(
                update={"files": [*change_set.files, change]}
            )
            if updated.lifecycle is ChangeLifecycle.APPLIED:
                updated = updated.model_copy(
                    update={"reversibility": self._reversibility(updated)}
                )
            self._store.save(updated)
            self._store.delete_pending(pending.id)
            return
        raise PendingMutationConflict(
            f"Pending mutation for {pending.relative_path} conflicts."
        )

    def _reconcile_operation(
        self,
        tree: WorkspaceTreeTransaction,
        action: str,
        pending_items: list[PendingMutation],
    ) -> None:
        change_set_ids = {pending.change_set_id for pending in pending_items}
        if len(change_set_ids) != 1:
            raise PendingMutationConflict(
                "Interrupted change operation has inconsistent ownership."
            )
        try:
            change_set = self._owned(next(iter(change_set_ids)))
        except (ChangeLifecycleError, ChangeSetNotFound) as error:
            raise PendingMutationConflict(
                "Interrupted change operation has inconsistent ownership."
            ) from error
        committed_lifecycle = (
            ChangeLifecycle.UNDONE if action == "undo" else ChangeLifecycle.APPLIED
        )
        source_lifecycle = (
            ChangeLifecycle.APPLIED if action == "undo" else ChangeLifecycle.UNDONE
        )
        if change_set.lifecycle not in {source_lifecycle, committed_lifecycle}:
            raise PendingMutationConflict(
                "Interrupted change operation has an invalid lifecycle."
            )

        bound: list[
            tuple[
                PendingMutation,
                BoundWorkspaceNode,
                NodeSnapshot | None,
                NodeSnapshot | None,
            ]
        ] = []
        for pending in sorted(pending_items, key=lambda item: item.id):
            relative = self._recovery_relative(pending.relative_path)
            target = tree.bind(relative)
            before = self._pending_snapshot(pending, before=True)
            intended = self._pending_snapshot(pending, before=False)
            if not (
                snapshots_match(target.snapshot, before)
                or snapshots_match(target.snapshot, intended)
            ):
                raise PendingMutationConflict(
                    f"Interrupted change operation for {relative} conflicts."
                )
            bound.append((pending, target, before, intended))

        if change_set.lifecycle is committed_lifecycle:
            if any(
                not snapshots_match(tree.capture(target), intended)
                for _, target, _, intended in bound
            ):
                raise PendingMutationConflict(
                    "Committed change operation does not match its filesystem state."
                )
        else:
            for pending, target, before, intended in reversed(bound):
                current_snapshot = tree.capture(target)
                if snapshots_match(current_snapshot, before):
                    continue
                assert snapshots_match(current_snapshot, intended)
                restored = tree.restore(target, before)
                if not snapshots_match(restored, before):
                    raise PendingMutationConflict(
                        f"Interrupted change operation for {pending.relative_path} "
                        "could not be rolled back."
                    )
            for pending, _, before, _ in bound:
                current = tree.bind(pending.relative_path)
                if not snapshots_match(current.snapshot, before):
                    raise PendingMutationConflict(
                        "Interrupted change operation remains partially applied."
                    )

        for pending, _, _, _ in bound:
            self._store.delete_pending(pending.id)

    def reconcile_pending(self, *, change_set_id: str | None = None) -> None:
        pending_items = [
            pending
            for pending in self._store.list_pending()
            if pending.workspace_key == self._workspace.key
            and (change_set_id is None or pending.change_set_id == change_set_id)
        ]
        operations: dict[tuple[str, str], list[PendingMutation]] = {}
        ordinary: dict[str, list[PendingMutation]] = {}
        for pending in pending_items:
            action = self._operation_action(pending)
            if action is None:
                ordinary.setdefault(pending.change_set_id, []).append(pending)
            else:
                operations.setdefault((pending.change_set_id, action), []).append(
                    pending
                )
        try:
            with WorkspaceTreeTransaction(self._workspace) as tree:
                operation_change_sets = {
                    change_set_id for change_set_id, _ in operations
                }
                for change_set_id, pending_items in ordinary.items():
                    for pending in pending_items:
                        self._reconcile_mutation(tree, pending)
                    change_set = self._owned(change_set_id)
                    if (
                        change_set.lifecycle is ChangeLifecycle.OPEN
                        and change_set_id not in operation_change_sets
                    ):
                        self.seal(change_set_id)
                for (_, action), operation_items in operations.items():
                    self._reconcile_operation(tree, action, operation_items)
        except (MutationTargetChanged, UnsafeWorkspacePath, OSError) as error:
            raise PendingMutationConflict(
                "Pending mutation could not be inspected safely and was preserved."
            ) from error

    def seal_orphaned_open(self) -> None:
        for change_set in self._store.list_open(self._workspace.key):
            self.seal(change_set.id)
