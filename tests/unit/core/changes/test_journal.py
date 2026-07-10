import hashlib
import stat
from pathlib import Path

import pytest

import awesome_agent.core.changes.journal as journal_module
from awesome_agent.core.changes import (
    ChangeLifecycle,
    ChangeReversibility,
    ChangeSet,
    FileChangeKind,
    FileNodeType,
)
from awesome_agent.core.changes.errors import (
    ChangeCapacityExceeded,
    PendingMutationConflict,
)
from awesome_agent.core.changes.journal import ChangeJournal, NodeSnapshot
from awesome_agent.core.changes.ports import PendingMutation
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


def write_bytes(path: Path, content: bytes) -> None:
    path.write_bytes(content)


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
        path=path,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(
            node_type=FileNodeType.FILE,
            content=b"after",
            mode=mode,
        ),
        mutate=lambda: write_bytes(path, b"after"),
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
        path=path,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
        mutate=lambda: write_bytes(path, b"after"),
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
            path=path,
            kind=FileChangeKind.UPDATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
            mutate=mutate,
        )

    assert mutated is False
    assert path.read_bytes() == b"before"


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
            path=path,
            kind=FileChangeKind.UPDATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
            mutate=mutate,
        )

    assert mutated is False
    assert path.read_bytes() == b"before"


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


def test_reconcile_preserves_conflicting_pending_mutation(tmp_path: Path) -> None:
    journal, store, _, pending = pending_fixture(tmp_path, b"user edit")

    with pytest.raises(PendingMutationConflict):
        journal.reconcile_pending()

    assert store.list_pending() == [pending]
