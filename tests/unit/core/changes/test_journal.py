import hashlib
import os
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

import awesome_agent.core.changes.journal as journal_module
from awesome_agent.core.changes import (
    BoundFileMutation,
    ChangeLifecycle,
    ChangeOperations,
    ChangeReversibility,
    ChangeSet,
    FileChangeKind,
    FileNodeType,
    NodeSnapshot,
    merge_file_changes,
)
from awesome_agent.core.changes.errors import (
    ChangeCapacityExceeded,
    ChangeLifecycleError,
    PendingMutationConflict,
)
from awesome_agent.core.changes.filesystem import (
    BoundWorkspaceNode,
    WorkspaceTreeTransaction,
)
from awesome_agent.core.changes.journal import ChangeJournal
from awesome_agent.core.changes.ports import PendingMutation
from awesome_agent.core.filesystem import MutationTargetChanged
from awesome_agent.core.workspace import resolve_workspace


class MemoryChangeSetStore:
    def __init__(self) -> None:
        self.change_sets: dict[str, ChangeSet] = {}
        self.pending: dict[str, PendingMutation] = {}

    def save(self, change_set: ChangeSet) -> None:
        self.change_sets[change_set.id] = change_set

    def get(self, change_set_id: str) -> ChangeSet | None:
        return self.change_sets.get(change_set_id)

    def latest(self, workspace_key: str) -> ChangeSet | None:
        matches = [
            item
            for item in self.change_sets.values()
            if item.workspace_key == workspace_key
        ]
        return max(matches, key=lambda item: item.created_at, default=None)

    def list_open(self, workspace_key: str) -> list[ChangeSet]:
        return [
            item
            for item in self.change_sets.values()
            if item.workspace_key == workspace_key
            and item.lifecycle is ChangeLifecycle.OPEN
        ]

    def save_pending(self, pending: PendingMutation) -> None:
        self.pending[pending.id] = pending

    def list_pending(self) -> list[PendingMutation]:
        return list(self.pending.values())

    def delete_pending(self, pending_id: str) -> None:
        self.pending.pop(pending_id, None)


class MemoryBlobStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        self.blobs[digest] = content
        return digest

    def get(self, digest: str) -> bytes:
        return self.blobs[digest]


def journal_fixture(
    tmp_path: Path,
) -> tuple[ChangeJournal, MemoryChangeSetStore, MemoryBlobStore, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = MemoryChangeSetStore()
    blobs = MemoryBlobStore()
    journal = ChangeJournal(store, blobs, resolve_workspace(workspace))
    return journal, store, blobs, workspace


def test_begin_rejects_rebound_identity_at_same_workspace_path(
    tmp_path: Path,
) -> None:
    journal, store, _, workspace = journal_fixture(tmp_path)
    original = tmp_path / "workspace-original"
    workspace.rename(original)
    workspace.mkdir()

    with pytest.raises(ChangeLifecycleError, match="different workspace"):
        journal.begin(
            session_id="session_1",
            turn_id="turn_1",
            workspace=resolve_workspace(workspace),
        )

    assert store.change_sets == {}


def write_bytes(path: Path, content: bytes) -> None:
    path.write_bytes(content)


def write_bytes_mutation(path: Path, content: bytes) -> Callable[[], None]:
    def mutate() -> None:
        write_bytes(path, content)

    return mutate


def bound_path_mutation(
    workspace: Path,
    path: Path,
    mutate: Callable[[], None],
) -> BoundFileMutation:
    def capture() -> NodeSnapshot | None:
        if not path.exists():
            return None
        return NodeSnapshot(
            FileNodeType.FILE,
            path.read_bytes(),
            stat.S_IMODE(path.stat().st_mode),
        )

    return BoundFileMutation(
        relative_path=path.relative_to(workspace).as_posix(),
        before=capture(),
        mutate=mutate,
        capture_after=capture,
    )


def test_controlled_change_seals_as_fully_reversible(tmp_path: Path) -> None:
    journal, _, _, workspace = journal_fixture(tmp_path)
    path = workspace / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )

    change = journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(
            node_type=FileNodeType.FILE,
            content=b"after",
            mode=mode,
        ),
        target=bound_path_mutation(
            workspace,
            path,
            lambda: write_bytes(path, b"after"),
        ),
    )
    sealed = journal.seal(change_set.id)

    assert change.before_blob is not None
    assert change.after_blob is not None
    assert sealed.lifecycle is ChangeLifecycle.APPLIED
    assert sealed.reversibility is ChangeReversibility.FULL


def test_execute_reversibility_is_partial_or_none(tmp_path: Path) -> None:
    journal, _, _, workspace = journal_fixture(tmp_path)
    mixed = journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    path = workspace / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    journal.apply_file_mutation(
        change_set_id=mixed.id,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
        target=bound_path_mutation(
            workspace,
            path,
            lambda: write_bytes(path, b"after"),
        ),
    )
    journal.record_execute(
        change_set_id=mixed.id,
        command="pytest",
        observed_paths=[],
    )
    assert journal.seal(mixed.id).reversibility is ChangeReversibility.PARTIAL

    execute_only = journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    journal.record_execute(
        change_set_id=execute_only.id,
        command="pytest",
        observed_paths=[],
    )
    assert journal.seal(execute_only.id).reversibility is ChangeReversibility.NONE


def test_capacity_is_rejected_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _, _, workspace = journal_fixture(tmp_path)
    path = workspace / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    mutated = False

    def mutate() -> None:
        nonlocal mutated
        mutated = True
        path.write_bytes(b"after")

    monkeypatch.setattr(journal_module, "MAX_CHANGESET_BYTES", 3)
    with pytest.raises(ChangeCapacityExceeded):
        journal.apply_file_mutation(
            change_set_id=change_set.id,
            kind=FileChangeKind.UPDATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
            target=bound_path_mutation(workspace, path, mutate),
        )

    assert mutated is False
    assert path.read_bytes() == b"before"


def test_bound_target_change_clears_pending_without_recapturing_a_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, store, _, workspace = journal_fixture(tmp_path)
    path = workspace / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )

    def reject_changed_target() -> None:
        raise MutationTargetChanged("changed")

    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: pytest.fail("Bound mutation must not recapture a raw path."),
    )
    with pytest.raises(MutationTargetChanged):
        journal.apply_file_mutation(
            change_set_id=change_set.id,
            kind=FileChangeKind.UPDATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
            target=BoundFileMutation(
                relative_path="file.txt",
                before=NodeSnapshot(FileNodeType.FILE, b"before", mode),
                mutate=reject_changed_target,
                capture_after=lambda: pytest.fail(
                    "Rejected mutation must not capture an after state."
                ),
            ),
        )

    assert store.list_pending() == []
    with path.open("rb") as stream:
        assert stream.read() == b"before"


@pytest.mark.parametrize("relative_path", [r"\outside.txt", "/outside.txt"])
def test_bound_mutation_rejects_windows_rooted_relative_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
) -> None:
    journal, _, _, workspace = journal_fixture(tmp_path)
    change_set = journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    monkeypatch.setattr(
        "awesome_agent.core.workspace.path_syntax.workspace_path_platform",
        lambda: "windows",
    )

    with pytest.raises(ChangeLifecycleError, match="Mutation path escapes"):
        journal.apply_file_mutation(
            change_set_id=change_set.id,
            kind=FileChangeKind.CREATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, b"content", None),
            target=BoundFileMutation(
                relative_path=relative_path,
                before=None,
                mutate=lambda: pytest.fail("Rooted mutation must not run."),
                capture_after=lambda: pytest.fail(
                    "Rooted mutation must not capture state."
                ),
            ),
        )


@pytest.mark.parametrize(
    "relative_path",
    ["file.txt:stream", ".env. ", "CON.txt", "dir/NUL.txt"],
)
def test_bound_mutation_rejects_windows_aliases_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
) -> None:
    journal, _, _, workspace = journal_fixture(tmp_path)
    change_set = journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    monkeypatch.setattr(
        "awesome_agent.core.workspace.path_syntax.workspace_path_platform",
        lambda: "windows",
    )

    with pytest.raises(ChangeLifecycleError, match="aliases the workspace"):
        journal.apply_file_mutation(
            change_set_id=change_set.id,
            kind=FileChangeKind.CREATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, b"content", None),
            target=BoundFileMutation(
                relative_path=relative_path,
                before=None,
                mutate=lambda: pytest.fail("Aliased mutation must not run."),
                capture_after=lambda: pytest.fail(
                    "Aliased mutation must not capture state."
                ),
            ),
        )


def test_file_count_capacity_is_rejected_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _, _, workspace = journal_fixture(tmp_path)
    path = workspace / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    mutated = False

    def mutate() -> None:
        nonlocal mutated
        mutated = True

    monkeypatch.setattr(journal_module, "MAX_CHANGESET_FILES", 0)
    with pytest.raises(ChangeCapacityExceeded):
        journal.apply_file_mutation(
            change_set_id=change_set.id,
            kind=FileChangeKind.UPDATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
            target=bound_path_mutation(workspace, path, mutate),
        )

    assert mutated is False
    assert path.read_bytes() == b"before"


def test_seal_reconciles_or_preserves_ordinary_pending_mutation(
    tmp_path: Path,
) -> None:
    journal, store, _, workspace = journal_fixture(tmp_path)
    path = workspace / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    before = NodeSnapshot(FileNodeType.FILE, b"before", mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )

    def capture_fails() -> NodeSnapshot | None:
        raise OSError("capture failed after mutation")

    def mutate() -> None:
        path.write_bytes(b"after")

    with pytest.raises(OSError, match="capture failed"):
        journal.apply_file_mutation(
            change_set_id=change_set.id,
            kind=FileChangeKind.UPDATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
            target=BoundFileMutation(
                relative_path="file.txt",
                before=before,
                mutate=mutate,
                capture_after=capture_fails,
            ),
        )

    with pytest.raises(PendingMutationConflict, match="unresolved pending"):
        journal.seal(change_set.id)
    retained = store.get(change_set.id)
    assert retained is not None
    assert retained.lifecycle is ChangeLifecycle.OPEN
    assert len(store.list_pending()) == 1

    journal.reconcile_pending()
    sealed = store.get(change_set.id)

    assert sealed is not None
    assert sealed.lifecycle is ChangeLifecycle.APPLIED
    assert [change.path for change in sealed.files] == ["file.txt"]
    assert store.list_pending() == []


def pending_fixture(
    tmp_path: Path,
    current: bytes,
) -> tuple[ChangeJournal, MemoryChangeSetStore, Path, PendingMutation]:
    journal, store, blobs, workspace = journal_fixture(tmp_path)
    path = workspace / "file.txt"
    path.write_bytes(current)
    mode = stat.S_IMODE(path.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    before = b"before"
    after = b"after"
    pending = PendingMutation(
        id="pending_1",
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path="file.txt",
        kind=FileChangeKind.UPDATED,
        node_type=FileNodeType.FILE,
        before_hash=hashlib.sha256(before).hexdigest(),
        before_blob=blobs.put(before),
        before_mode=mode,
        intended_after_hash=hashlib.sha256(after).hexdigest(),
        intended_after_blob=blobs.put(after),
        intended_after_mode=mode,
        created_at=change_set.created_at,
    )
    store.save_pending(pending)
    return journal, store, path, pending


class ParentReplacement:
    def __init__(self, parent: Path, outside: Path) -> None:
        self.parent = parent
        self.outside = outside
        self.original = parent.with_name(f"{parent.name}.original")
        self.replaced = False

    def trigger(self) -> None:
        self.parent.rename(self.original)
        if os.name == "nt":
            completed = subprocess.run(
                [
                    os.environ.get("COMSPEC", "cmd.exe"),
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(self.parent),
                    str(self.outside),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            assert completed.returncode == 0, completed.stderr
        else:
            self.parent.symlink_to(self.outside, target_is_directory=True)
        self.replaced = True

    def restore(self) -> None:
        if not self.replaced:
            return
        if os.name == "nt":
            self.parent.rmdir()
        else:
            self.parent.unlink()
        self.original.rename(self.parent)


def interrupted_undo_fixture(
    tmp_path: Path,
) -> tuple[ChangeJournal, MemoryChangeSetStore, Path, PendingMutation]:
    journal, store, blobs, workspace = journal_fixture(tmp_path)
    path = workspace / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
        target=bound_path_mutation(
            workspace,
            path,
            lambda: write_bytes(path, b"after"),
        ),
    )
    journal.seal(change_set.id)
    path.write_bytes(b"before")
    pending = PendingMutation(
        id="undo_operation_0",
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path="file.txt",
        kind=FileChangeKind.UPDATED,
        node_type=FileNodeType.FILE,
        before_hash=hashlib.sha256(b"after").hexdigest(),
        before_blob=blobs.put(b"after"),
        before_mode=mode,
        intended_after_hash=hashlib.sha256(b"before").hexdigest(),
        intended_after_blob=blobs.put(b"before"),
        intended_after_mode=mode,
        created_at=change_set.created_at,
    )
    store.save_pending(pending)
    return journal, store, path, pending


def test_reconcile_discards_mutation_that_never_happened(tmp_path: Path) -> None:
    journal, store, _, pending = pending_fixture(tmp_path, b"before")

    journal.reconcile_pending()

    assert store.list_pending() == []
    change_set = store.get(pending.change_set_id)
    assert change_set is not None
    assert change_set.files == []


def test_reconcile_finalizes_completed_mutation(tmp_path: Path) -> None:
    journal, store, _, pending = pending_fixture(tmp_path, b"after")

    journal.reconcile_pending()

    change_set = store.get(pending.change_set_id)
    assert change_set is not None
    assert len(change_set.files) == 1
    assert change_set.files[0].after_hash == pending.intended_after_hash
    assert store.list_pending() == []


def test_reconcile_keeps_a_repeated_transition_as_a_distinct_mutation(
    tmp_path: Path,
) -> None:
    journal, store, blobs, workspace = journal_fixture(tmp_path)
    path = workspace / "file.txt"
    path.write_bytes(b"A")
    mode = stat.S_IMODE(path.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )

    for before, after in ((b"A", b"B"), (b"B", b"A")):
        assert path.read_bytes() == before
        journal.apply_file_mutation(
            change_set_id=change_set.id,
            kind=FileChangeKind.UPDATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, after, mode),
            target=bound_path_mutation(
                workspace,
                path,
                write_bytes_mutation(path, after),
            ),
        )

    pending = PendingMutation(
        id="pending_third_a_to_b",
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path="file.txt",
        kind=FileChangeKind.UPDATED,
        node_type=FileNodeType.FILE,
        before_hash=hashlib.sha256(b"A").hexdigest(),
        before_blob=blobs.put(b"A"),
        before_mode=mode,
        intended_after_hash=hashlib.sha256(b"B").hexdigest(),
        intended_after_blob=blobs.put(b"B"),
        intended_after_mode=mode,
        created_at=change_set.created_at,
    )
    store.save_pending(pending)
    path.write_bytes(b"B")

    journal.reconcile_pending()

    recovered = store.get(change_set.id)
    assert recovered is not None
    assert len(recovered.files) == 3
    merged = merge_file_changes(recovered.files)
    assert len(merged) == 1
    assert merged[0].before_hash == hashlib.sha256(b"A").hexdigest()
    assert merged[0].after_hash == hashlib.sha256(b"B").hexdigest()
    assert store.list_pending() == []

    operations = ChangeOperations(
        store,
        blobs,
        resolve_workspace(workspace),
    )
    operations.undo(change_set.id)
    assert path.read_bytes() == b"A"
    operations.redo(change_set.id)
    assert path.read_bytes() == b"B"


def test_reconcile_does_not_append_the_same_persisted_mutation_twice(
    tmp_path: Path,
) -> None:
    journal, store, blobs, workspace = journal_fixture(tmp_path)
    path = workspace / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )
    recorded = journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
        target=bound_path_mutation(
            workspace,
            path,
            lambda: write_bytes(path, b"after"),
        ),
    )
    assert recorded.mutation_id is not None
    pending = PendingMutation(
        id=recorded.mutation_id,
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path=recorded.path,
        kind=recorded.kind,
        node_type=recorded.node_type,
        before_hash=recorded.before_hash,
        before_blob=recorded.before_blob,
        before_mode=recorded.before_mode,
        intended_after_hash=recorded.after_hash,
        intended_after_blob=recorded.after_blob,
        intended_after_mode=recorded.after_mode,
        created_at=change_set.created_at,
    )
    assert pending.intended_after_blob is not None
    assert blobs.get(pending.intended_after_blob) == b"after"
    store.save_pending(pending)

    journal.reconcile_pending()
    journal.reconcile_pending()

    recovered = store.get(change_set.id)
    assert recovered is not None
    assert recovered.files == [recorded]
    assert store.list_pending() == []


def test_reconcile_preserves_a_committed_mutation_reverted_to_before(
    tmp_path: Path,
) -> None:
    journal, store, _, workspace = journal_fixture(tmp_path)
    path = workspace / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )
    recorded = journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
        target=bound_path_mutation(
            workspace,
            path,
            lambda: write_bytes(path, b"after"),
        ),
    )
    assert recorded.mutation_id is not None
    pending = PendingMutation(
        id=recorded.mutation_id,
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path=recorded.path,
        kind=recorded.kind,
        node_type=recorded.node_type,
        before_hash=recorded.before_hash,
        before_blob=recorded.before_blob,
        before_mode=recorded.before_mode,
        intended_after_hash=recorded.after_hash,
        intended_after_blob=recorded.after_blob,
        intended_after_mode=recorded.after_mode,
        created_at=change_set.created_at,
    )
    store.save_pending(pending)
    path.write_bytes(b"before")

    with pytest.raises(PendingMutationConflict, match="committed mutation"):
        journal.reconcile_pending()

    retained = store.get(change_set.id)
    assert retained is not None
    assert retained.lifecycle is ChangeLifecycle.OPEN
    assert retained.files == [recorded]
    assert store.list_pending() == [pending]


def test_reconcile_finalizes_a_committed_create_with_an_unconstrained_mode(
    tmp_path: Path,
) -> None:
    journal, store, _, workspace = journal_fixture(tmp_path)
    path = workspace / "created.txt"
    change_set = journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )
    recorded = journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.CREATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"content", None),
        target=bound_path_mutation(
            workspace,
            path,
            lambda: write_bytes(path, b"content"),
        ),
    )
    assert recorded.mutation_id is not None
    assert recorded.after_mode is not None
    pending = PendingMutation(
        id=recorded.mutation_id,
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path=recorded.path,
        kind=recorded.kind,
        node_type=recorded.node_type,
        before_node_type=None,
        intended_after_node_type=FileNodeType.FILE,
        before_hash=None,
        before_blob=None,
        before_mode=None,
        intended_after_hash=recorded.after_hash,
        intended_after_blob=recorded.after_blob,
        intended_after_mode=None,
        created_at=change_set.created_at,
    )
    store.save_pending(pending)

    journal.reconcile_pending()

    recovered = store.get(change_set.id)
    assert recovered is not None
    assert recovered.files == [recorded]
    assert store.list_pending() == []


def test_reconcile_preserves_a_committed_create_if_actual_mode_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, store, _, workspace = journal_fixture(tmp_path)
    path = workspace / "created.txt"
    change_set = journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )
    recorded = journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.CREATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"content", None),
        target=bound_path_mutation(
            workspace,
            path,
            lambda: write_bytes(path, b"content"),
        ),
    )
    assert recorded.mutation_id is not None
    assert recorded.after_mode is not None
    pending = PendingMutation(
        id=recorded.mutation_id,
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path=recorded.path,
        kind=recorded.kind,
        node_type=recorded.node_type,
        before_node_type=None,
        intended_after_node_type=FileNodeType.FILE,
        before_hash=None,
        before_blob=None,
        before_mode=None,
        intended_after_hash=recorded.after_hash,
        intended_after_blob=recorded.after_blob,
        intended_after_mode=None,
        created_at=change_set.created_at,
    )
    store.save_pending(pending)
    original_capture = WorkspaceTreeTransaction.capture

    def capture_with_changed_mode(
        tree: WorkspaceTreeTransaction,
        target: BoundWorkspaceNode,
    ) -> NodeSnapshot | None:
        snapshot = original_capture(tree, target)
        assert snapshot is not None
        assert snapshot.mode is not None
        return NodeSnapshot(
            snapshot.node_type,
            snapshot.content,
            snapshot.mode ^ stat.S_IXUSR,
        )

    monkeypatch.setattr(
        WorkspaceTreeTransaction,
        "capture",
        capture_with_changed_mode,
    )

    with pytest.raises(PendingMutationConflict, match="committed mutation"):
        journal.reconcile_pending()

    assert store.list_pending() == [pending]


def test_reconcile_preserves_legacy_mutation_identity_ambiguity(
    tmp_path: Path,
) -> None:
    journal, store, _, workspace = journal_fixture(tmp_path)
    path = workspace / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )
    recorded = journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
        target=bound_path_mutation(
            workspace,
            path,
            lambda: write_bytes(path, b"after"),
        ),
    )
    assert recorded.mutation_id is not None
    legacy = recorded.model_copy(update={"mutation_id": None})
    store.save(change_set.model_copy(update={"files": [legacy]}))
    pending = PendingMutation(
        id=recorded.mutation_id,
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path=recorded.path,
        kind=recorded.kind,
        node_type=recorded.node_type,
        before_hash=recorded.before_hash,
        before_blob=recorded.before_blob,
        before_mode=recorded.before_mode,
        intended_after_hash=recorded.after_hash,
        intended_after_blob=recorded.after_blob,
        intended_after_mode=recorded.after_mode,
        created_at=change_set.created_at,
    )
    store.save_pending(pending)

    with pytest.raises(PendingMutationConflict, match="legacy mutation identity"):
        journal.reconcile_pending()

    retained = store.get(change_set.id)
    assert retained is not None
    assert retained.files == [legacy]
    assert store.list_pending() == [pending]


def test_unresolved_pending_blocks_a_second_file_mutation(tmp_path: Path) -> None:
    journal, store, blobs, workspace = journal_fixture(tmp_path)
    first = workspace / "first.txt"
    second = workspace / "second.txt"
    first.write_bytes(b"before")
    second.write_bytes(b"before")
    mode = stat.S_IMODE(first.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )
    pending = PendingMutation(
        id="pending_first",
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path="first.txt",
        kind=FileChangeKind.UPDATED,
        node_type=FileNodeType.FILE,
        before_hash=hashlib.sha256(b"before").hexdigest(),
        before_blob=blobs.put(b"before"),
        before_mode=mode,
        intended_after_hash=hashlib.sha256(b"after").hexdigest(),
        intended_after_blob=blobs.put(b"after"),
        intended_after_mode=mode,
        created_at=change_set.created_at,
    )
    store.save_pending(pending)
    mutated = False

    def mutate() -> None:
        nonlocal mutated
        mutated = True
        second.write_bytes(b"after")

    with pytest.raises(PendingMutationConflict, match="unresolved pending"):
        journal.apply_file_mutation(
            change_set_id=change_set.id,
            kind=FileChangeKind.UPDATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
            target=bound_path_mutation(workspace, second, mutate),
        )

    assert mutated is False
    assert second.read_bytes() == b"before"
    assert store.list_pending() == [pending]

    with pytest.raises(PendingMutationConflict, match="unresolved pending"):
        journal.preflight_batch(
            change_set_id=change_set.id,
            additional_nodes=1,
            additional_bytes=1,
        )
    with pytest.raises(PendingMutationConflict, match="unresolved pending"):
        journal.record_execute(
            change_set_id=change_set.id,
            command="echo should-not-run",
            observed_paths=[],
        )
    retained = store.get(change_set.id)
    assert retained is not None
    assert retained.execute == []


def test_reconcile_cannot_exceed_the_file_count_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, store, blobs, workspace = journal_fixture(tmp_path)
    first = workspace / "first.txt"
    second = workspace / "second.txt"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    first_mode = stat.S_IMODE(first.stat().st_mode)
    second_mode = stat.S_IMODE(second.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )
    journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"committed", first_mode),
        target=bound_path_mutation(
            workspace,
            first,
            lambda: write_bytes(first, b"committed"),
        ),
    )
    pending = PendingMutation(
        id="pending_over_file_limit",
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path="second.txt",
        kind=FileChangeKind.UPDATED,
        node_type=FileNodeType.FILE,
        before_hash=hashlib.sha256(b"before").hexdigest(),
        before_blob=blobs.put(b"before"),
        before_mode=second_mode,
        intended_after_hash=hashlib.sha256(b"second").hexdigest(),
        intended_after_blob=blobs.put(b"second"),
        intended_after_mode=second_mode,
        created_at=change_set.created_at,
    )
    store.save_pending(pending)
    monkeypatch.setattr(journal_module, "MAX_CHANGESET_FILES", 1)

    with pytest.raises(ChangeCapacityExceeded, match="file limit"):
        journal.reconcile_pending()

    retained = store.get(change_set.id)
    assert retained is not None
    assert len(retained.files) == 1
    assert store.list_pending() == [pending]


def test_reconcile_cannot_exceed_the_byte_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, store, blobs, workspace = journal_fixture(tmp_path)
    first = workspace / "first.txt"
    second = workspace / "second.txt"
    first.write_bytes(b"a")
    second.write_bytes(b"12345")
    first_mode = stat.S_IMODE(first.stat().st_mode)
    second_mode = stat.S_IMODE(second.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )
    journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"b", first_mode),
        target=bound_path_mutation(
            workspace,
            first,
            lambda: write_bytes(first, b"b"),
        ),
    )
    pending = PendingMutation(
        id="pending_over_byte_limit",
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path="second.txt",
        kind=FileChangeKind.CREATED,
        node_type=FileNodeType.FILE,
        before_hash=None,
        before_blob=None,
        before_mode=None,
        intended_after_hash=hashlib.sha256(b"12345").hexdigest(),
        intended_after_blob=blobs.put(b"12345"),
        intended_after_mode=second_mode,
        created_at=change_set.created_at,
    )
    store.save_pending(pending)
    monkeypatch.setattr(journal_module, "MAX_CHANGESET_BYTES", 6)

    with pytest.raises(ChangeCapacityExceeded, match="byte limit"):
        journal.reconcile_pending()

    retained = store.get(change_set.id)
    assert retained is not None
    assert len(retained.files) == 1
    assert store.list_pending() == [pending]


def test_reconcile_repairs_legacy_sealed_ordinary_pending(tmp_path: Path) -> None:
    journal, store, _, pending = pending_fixture(tmp_path, b"after")
    change_set = store.get(pending.change_set_id)
    assert change_set is not None
    store.save(
        change_set.model_copy(
            update={
                "lifecycle": ChangeLifecycle.APPLIED,
                "sealed_at": change_set.created_at,
            }
        )
    )

    journal.reconcile_pending()

    repaired = store.get(pending.change_set_id)
    assert repaired is not None
    assert repaired.lifecycle is ChangeLifecycle.APPLIED
    assert [change.path for change in repaired.files] == ["file.txt"]
    assert store.list_pending() == []


def test_reconcile_preserves_conflicting_pending_mutation(tmp_path: Path) -> None:
    journal, store, _, pending = pending_fixture(tmp_path, b"user edit")

    with pytest.raises(PendingMutationConflict):
        journal.reconcile_pending()

    assert store.list_pending() == [pending]


def test_reconcile_rolls_back_an_interrupted_uncommitted_undo(
    tmp_path: Path,
) -> None:
    journal, store, path, _ = interrupted_undo_fixture(tmp_path)

    journal.reconcile_pending()

    assert path.read_bytes() == b"after"
    assert store.list_pending() == []


def test_reconcile_finalizes_an_interrupted_committed_undo(tmp_path: Path) -> None:
    journal, store, path, pending = interrupted_undo_fixture(tmp_path)
    change_set = store.get(pending.change_set_id)
    assert change_set is not None
    store.save(change_set.model_copy(update={"lifecycle": ChangeLifecycle.UNDONE}))

    journal.reconcile_pending()

    assert path.read_bytes() == b"before"
    assert store.list_pending() == []
    committed = store.get(pending.change_set_id)
    assert committed is not None
    assert committed.lifecycle is ChangeLifecycle.UNDONE


def test_reconcile_rejects_parent_replacement_without_reading_external_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, store, blobs, workspace = journal_fixture(tmp_path)
    parent = workspace / "parent"
    parent.mkdir()
    path = parent / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    change_set = journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    pending = PendingMutation(
        id="pending_parent_race",
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path="parent/file.txt",
        kind=FileChangeKind.UPDATED,
        node_type=FileNodeType.FILE,
        before_hash=hashlib.sha256(b"before").hexdigest(),
        before_blob=blobs.put(b"before"),
        before_mode=mode,
        intended_after_hash=hashlib.sha256(b"after").hexdigest(),
        intended_after_blob=blobs.put(b"after"),
        intended_after_mode=mode,
        created_at=change_set.created_at,
    )
    store.save_pending(pending)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "file.txt"
    sentinel.write_bytes(b"after")
    replacement = ParentReplacement(parent, outside)
    original_list_pending = store.list_pending

    def list_pending_with_race() -> list[PendingMutation]:
        result = original_list_pending()
        replacement.trigger()
        return result

    monkeypatch.setattr(store, "list_pending", list_pending_with_race)
    try:
        with pytest.raises(PendingMutationConflict):
            journal.reconcile_pending()
    finally:
        replacement.restore()

    assert sentinel.read_bytes() == b"after"
    assert original_list_pending() == [pending]
    restored = store.get(change_set.id)
    assert restored is not None
    assert restored.files == []


def test_reconcile_rejects_hard_link_and_preserves_pending(tmp_path: Path) -> None:
    journal, store, path, pending = pending_fixture(tmp_path, b"before")
    sentinel = tmp_path / "outside.txt"
    sentinel.write_bytes(b"before")
    path.unlink()
    os.link(sentinel, path)

    with pytest.raises(PendingMutationConflict):
        journal.reconcile_pending()

    assert sentinel.read_bytes() == b"before"
    assert store.list_pending() == [pending]


@pytest.mark.parametrize("relative_path", [r"\outside.txt", "/outside.txt"])
def test_reconcile_rejects_windows_rooted_pending_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
) -> None:
    journal, store, _, pending = pending_fixture(tmp_path, b"before")
    rooted = pending.model_copy(update={"relative_path": relative_path})
    store.delete_pending(pending.id)
    store.save_pending(rooted)
    monkeypatch.setattr(
        "awesome_agent.core.workspace.path_syntax.workspace_path_platform",
        lambda: "windows",
    )

    with pytest.raises(PendingMutationConflict, match="workspace boundary"):
        journal.reconcile_pending()

    assert store.list_pending() == [rooted]
