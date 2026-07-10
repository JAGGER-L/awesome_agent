import stat
from functools import partial
from pathlib import Path

import pytest

from awesome_agent.core.changes import (
    ChangeJournal,
    ChangeLifecycle,
    ChangeOperations,
    ChangeReversibility,
    FileChangeKind,
    FileNodeType,
    NodeSnapshot,
)
from awesome_agent.core.changes.errors import ChangeConflict, ChangeNotReversible
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore


def write_bytes(path: Path, content: bytes) -> None:
    path.write_bytes(content)


def operations_fixture(
    tmp_path: Path,
) -> tuple[ChangeJournal, ChangeOperations, SQLiteChangeSetStore, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)
    store = SQLiteChangeSetStore(tmp_path / "application.db")
    blobs = FileChangeBlobStore(tmp_path / "change-journal")
    journal = ChangeJournal(store, blobs, identity)
    operations = ChangeOperations(store, blobs, identity)
    return journal, operations, store, workspace


def create_two_file_change(
    tmp_path: Path,
) -> tuple[
    ChangeOperations,
    str,
    Path,
    SQLiteChangeSetStore,
    FileChangeBlobStore,
]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)
    store = SQLiteChangeSetStore(tmp_path / "application.db")
    blobs = FileChangeBlobStore(tmp_path / "change-journal")
    journal = ChangeJournal(store, blobs, identity)
    operations = ChangeOperations(store, blobs, identity)
    first = workspace / "first.txt"
    second = workspace / "second.txt"
    first.write_bytes(b"before first\n")
    second.write_bytes(b"before second\n")
    change_set = journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )
    for path, content in (
        (first, b"after first\n"),
        (second, b"after second\n"),
    ):
        mode = stat.S_IMODE(path.stat().st_mode)
        journal.apply_file_mutation(
            change_set_id=change_set.id,
            path=path,
            kind=FileChangeKind.UPDATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, content, mode),
            mutate=partial(write_bytes, path, content),
        )
    journal.seal(change_set.id)
    return operations, change_set.id, workspace, store, blobs


def test_diff_renders_text_and_summarizes_binary(tmp_path: Path) -> None:
    journal, operations, _, workspace = operations_fixture(tmp_path)
    text = workspace / "text.txt"
    binary = workspace / "binary.bin"
    text.write_bytes(b"before\n")
    binary.write_bytes(b"before\x00binary")
    change_set = journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    for path, content in ((text, b"after\n"), (binary, b"after\x00binary")):
        mode = stat.S_IMODE(path.stat().st_mode)
        journal.apply_file_mutation(
            change_set_id=change_set.id,
            path=path,
            kind=FileChangeKind.UPDATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, content, mode),
            mutate=partial(write_bytes, path, content),
        )
    journal.seal(change_set.id)

    rendered = operations.diff(change_set.id)

    assert "--- a/text.txt" in rendered
    assert "+++ b/text.txt" in rendered
    assert "-before" in rendered
    assert "+after" in rendered
    assert "Binary change: binary.bin" in rendered


def test_undo_and_redo_restore_controlled_files_after_reopen(tmp_path: Path) -> None:
    _, change_set_id, workspace, store, blobs = create_two_file_change(tmp_path)
    reopened = ChangeOperations(
        store,
        blobs,
        resolve_workspace(workspace),
    )

    undone = reopened.undo(change_set_id)

    assert undone.lifecycle is ChangeLifecycle.UNDONE
    assert (workspace / "first.txt").read_bytes() == b"before first\n"
    assert (workspace / "second.txt").read_bytes() == b"before second\n"

    redone = reopened.redo(change_set_id)

    assert redone.lifecycle is ChangeLifecycle.APPLIED
    assert (workspace / "first.txt").read_bytes() == b"after first\n"
    assert (workspace / "second.txt").read_bytes() == b"after second\n"


def test_undo_conflict_changes_nothing_and_preserves_lifecycle(tmp_path: Path) -> None:
    operations, change_set_id, workspace, store, _ = create_two_file_change(tmp_path)
    (workspace / "second.txt").write_bytes(b"user edit\n")

    with pytest.raises(ChangeConflict):
        operations.undo(change_set_id)

    assert (workspace / "first.txt").read_bytes() == b"after first\n"
    assert (workspace / "second.txt").read_bytes() == b"user edit\n"
    change_set = store.get(change_set_id)
    assert change_set is not None
    assert change_set.lifecycle is ChangeLifecycle.APPLIED


def test_partial_undo_warns_and_execute_only_is_not_reversible(tmp_path: Path) -> None:
    journal, operations, _, workspace = operations_fixture(tmp_path)
    path = workspace / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    mixed = journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    journal.apply_file_mutation(
        change_set_id=mixed.id,
        path=path,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
        mutate=partial(write_bytes, path, b"after"),
    )
    journal.record_execute(
        change_set_id=mixed.id,
        command="pytest",
        observed_paths=[],
    )
    assert journal.seal(mixed.id).reversibility is ChangeReversibility.PARTIAL

    result = operations.undo(mixed.id)

    assert result.warning is not None
    assert result.unmanaged_effects_restored is False

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
    journal.seal(execute_only.id)
    with pytest.raises(ChangeNotReversible):
        operations.undo(execute_only.id)
