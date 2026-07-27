import os
import stat
import subprocess
from collections.abc import AsyncIterator, Callable
from functools import partial
from pathlib import Path

import pytest

import awesome_agent.core.changes.operations as operations_module
from awesome_agent.core.changes import (
    BoundFileMutation,
    ChangeJournal,
    ChangeLifecycle,
    ChangeOperations,
    ChangeReversibility,
    FileChangeKind,
    FileNodeType,
    NodeSnapshot,
)
from awesome_agent.core.changes.errors import (
    ChangeConflict,
    ChangeLifecycleError,
    ChangeNotReversible,
)
from awesome_agent.core.changes.filesystem import (
    BoundWorkspaceNode,
    WorkspaceTreeTransaction,
)
from awesome_agent.core.changes.ports import PendingMutation
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage.application_sqlite import ApplicationSQLite
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore

type OperationsFixture = tuple[
    ChangeJournal,
    ChangeOperations,
    SQLiteChangeSetStore,
    FileChangeBlobStore,
    Path,
]


def write_bytes(path: Path, content: bytes) -> None:
    path.write_bytes(content)


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


@pytest.fixture
async def operations_fixture(
    tmp_path: Path,
) -> AsyncIterator[OperationsFixture]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    store = SQLiteChangeSetStore(database)
    blobs = FileChangeBlobStore(tmp_path / "change-journal")
    journal = ChangeJournal(store, blobs, identity)
    operations = ChangeOperations(store, blobs, identity)
    try:
        yield journal, operations, store, blobs, workspace
    finally:
        await database.aclose()


async def create_two_file_change(
    fixture: OperationsFixture,
) -> tuple[
    ChangeOperations,
    str,
    Path,
    SQLiteChangeSetStore,
    FileChangeBlobStore,
]:
    journal, operations, store, blobs, workspace = fixture
    first = workspace / "first.txt"
    second = workspace / "second.txt"
    first.write_bytes(b"before first\n")
    second.write_bytes(b"before second\n")
    change_set = await journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )
    for path, content in (
        (first, b"after first\n"),
        (second, b"after second\n"),
    ):
        mode = stat.S_IMODE(path.stat().st_mode)
        await journal.apply_file_mutation(
            change_set_id=change_set.id,
            kind=FileChangeKind.UPDATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, content, mode),
            target=bound_path_mutation(
                workspace,
                path,
                partial(write_bytes, path, content),
            ),
        )
    await journal.seal(change_set.id)
    return operations, change_set.id, workspace, store, blobs


async def create_directory_to_file_change(
    fixture: OperationsFixture,
) -> tuple[
    ChangeJournal,
    ChangeOperations,
    str,
    Path,
    SQLiteChangeSetStore,
]:
    journal, operations, store, _, workspace = fixture
    target = workspace / "node"
    target.mkdir()
    directory_mode = stat.S_IMODE(target.stat().st_mode)
    change_set = await journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )
    await journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.DELETED,
        intended_after=None,
        target=BoundFileMutation(
            relative_path="node",
            before=NodeSnapshot(
                FileNodeType.DIRECTORY,
                None,
                directory_mode,
            ),
            mutate=target.rmdir,
            capture_after=lambda: None,
        ),
    )
    content = b"replacement file\n"

    def capture_file() -> NodeSnapshot:
        return NodeSnapshot(
            FileNodeType.FILE,
            target.read_bytes(),
            stat.S_IMODE(target.stat().st_mode),
        )

    def write_replacement() -> None:
        target.write_bytes(content)

    await journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.CREATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, content, None),
        target=BoundFileMutation(
            relative_path="node",
            before=None,
            mutate=write_replacement,
            capture_after=capture_file,
        ),
    )
    await journal.seal(change_set.id)
    return journal, operations, change_set.id, target, store


class ParentReplacement:
    def __init__(self, parent: Path, outside: Path) -> None:
        self.parent = parent
        self.outside = outside
        self.original = parent.with_name(f"{parent.name}.original")
        self.replaced = False

    def trigger(self) -> None:
        try:
            self.parent.rename(self.original)
        except OSError:
            # Open Windows directory handles intentionally deny the rename.
            return
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


async def create_nested_file_change(
    fixture: OperationsFixture,
) -> tuple[
    ChangeOperations,
    str,
    Path,
    SQLiteChangeSetStore,
    FileChangeBlobStore,
]:
    journal, operations, store, blobs, workspace = fixture
    parent = workspace / "parent"
    parent.mkdir()
    path = parent / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    change_set = await journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    await journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
        target=bound_path_mutation(
            workspace,
            path,
            partial(write_bytes, path, b"after"),
        ),
    )
    await journal.seal(change_set.id)
    return operations, change_set.id, workspace, store, blobs


async def test_diff_renders_text_and_summarizes_binary(
    operations_fixture: OperationsFixture,
) -> None:
    journal, operations, _, _, workspace = operations_fixture
    text = workspace / "text.txt"
    binary = workspace / "binary.bin"
    text.write_bytes(b"before\n")
    binary.write_bytes(b"before\x00binary")
    change_set = await journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    for path, content in ((text, b"after\n"), (binary, b"after\x00binary")):
        mode = stat.S_IMODE(path.stat().st_mode)
        await journal.apply_file_mutation(
            change_set_id=change_set.id,
            kind=FileChangeKind.UPDATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, content, mode),
            target=bound_path_mutation(
                workspace,
                path,
                partial(write_bytes, path, content),
            ),
        )
    await journal.seal(change_set.id)

    rendered = await operations.diff(change_set.id)

    assert "--- a/text.txt" in rendered
    assert "+++ b/text.txt" in rendered
    assert "-before" in rendered
    assert "+after" in rendered
    assert "Binary change: binary.bin" in rendered


async def test_undo_and_redo_restore_controlled_files_after_reopen(
    operations_fixture: OperationsFixture,
) -> None:
    _, change_set_id, workspace, store, blobs = await create_two_file_change(
        operations_fixture
    )
    reopened = ChangeOperations(
        store,
        blobs,
        resolve_workspace(workspace),
    )

    undone = await reopened.undo(change_set_id)

    assert undone.lifecycle is ChangeLifecycle.UNDONE
    assert (workspace / "first.txt").read_bytes() == b"before first\n"
    assert (workspace / "second.txt").read_bytes() == b"before second\n"

    redone = await reopened.redo(change_set_id)

    assert redone.lifecycle is ChangeLifecycle.APPLIED
    assert (workspace / "first.txt").read_bytes() == b"after first\n"
    assert (workspace / "second.txt").read_bytes() == b"after second\n"


async def test_undo_and_redo_restore_a_directory_to_file_transition(
    operations_fixture: OperationsFixture,
) -> None:
    _, operations, change_set_id, target, _ = await create_directory_to_file_change(
        operations_fixture
    )

    assert "+replacement file" in await operations.diff(change_set_id)
    undone = await operations.undo(change_set_id)

    assert undone.lifecycle is ChangeLifecycle.UNDONE
    assert target.is_dir()

    redone = await operations.redo(change_set_id)

    assert redone.lifecycle is ChangeLifecycle.APPLIED
    assert target.is_file()
    assert target.read_bytes() == b"replacement file\n"


async def test_undo_and_redo_preserve_file_and_symlink_types_without_host_links(
    operations_fixture: OperationsFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, operations, store, _, workspace = operations_fixture
    before = NodeSnapshot(FileNodeType.FILE, b"file content", 0o644)
    after = NodeSnapshot(FileNodeType.SYMLINK, b"target.txt", 0o777)
    change_set = await journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=resolve_workspace(workspace),
    )
    await journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.UPDATED,
        intended_after=after,
        target=BoundFileMutation(
            relative_path="node",
            before=before,
            mutate=lambda: None,
            capture_after=lambda: after,
        ),
    )
    await journal.seal(change_set.id)

    class SnapshotTree:
        def __init__(self) -> None:
            self.snapshot: NodeSnapshot | None = after

        def __enter__(self) -> "SnapshotTree":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def bind(self, relative_path: str | Path) -> BoundWorkspaceNode:
            return BoundWorkspaceNode(
                relative=Path(relative_path),
                snapshot=self.snapshot,
                identity=None,
                missing_ancestor=None,
            )

        def restore(
            self,
            target: BoundWorkspaceNode,
            desired: NodeSnapshot | None,
        ) -> NodeSnapshot | None:
            del target
            self.snapshot = desired
            return desired

    tree = SnapshotTree()
    monkeypatch.setattr(
        operations_module,
        "WorkspaceTreeTransaction",
        lambda _workspace: tree,
    )

    await operations.undo(change_set.id)
    assert tree.snapshot == before

    await operations.redo(change_set.id)
    assert tree.snapshot == after
    persisted = await store.get(change_set.id)
    assert persisted is not None
    assert persisted.lifecycle is ChangeLifecycle.APPLIED
    assert await store.list_pending() == []


async def test_reconcile_rolls_back_an_interrupted_node_type_transition(
    operations_fixture: OperationsFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        journal,
        operations,
        change_set_id,
        target,
        store,
    ) = await create_directory_to_file_change(operations_fixture)
    original_restore = WorkspaceTreeTransaction.restore

    class SimulatedProcessInterruption(BaseException):
        pass

    def interrupt_after_restore(
        self: WorkspaceTreeTransaction,
        bound: BoundWorkspaceNode,
        desired: NodeSnapshot | None,
    ) -> NodeSnapshot | None:
        original_restore(self, bound, desired)
        raise SimulatedProcessInterruption

    monkeypatch.setattr(
        WorkspaceTreeTransaction,
        "restore",
        interrupt_after_restore,
    )
    with pytest.raises(SimulatedProcessInterruption):
        await operations.undo(change_set_id)

    assert target.is_dir()
    pending = await store.list_pending()
    assert len(pending) == 1
    assert pending[0].before_node_type is FileNodeType.FILE
    assert pending[0].intended_after_node_type is FileNodeType.DIRECTORY
    retained = await store.get(change_set_id)
    assert retained is not None
    assert retained.lifecycle is ChangeLifecycle.APPLIED

    monkeypatch.setattr(
        WorkspaceTreeTransaction,
        "restore",
        original_restore,
    )
    await journal.reconcile_pending()

    assert target.is_file()
    assert target.read_bytes() == b"replacement file\n"
    assert await store.list_pending() == []


async def test_reconcile_finalizes_a_committed_node_type_transition(
    operations_fixture: OperationsFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        journal,
        operations,
        change_set_id,
        target,
        store,
    ) = await create_directory_to_file_change(operations_fixture)
    original_delete_pending = store.delete_pending

    class SimulatedProcessInterruption(BaseException):
        pass

    async def interrupt_cleanup(_pending_id: str) -> None:
        raise SimulatedProcessInterruption

    monkeypatch.setattr(store, "delete_pending", interrupt_cleanup)
    with pytest.raises(SimulatedProcessInterruption):
        await operations.undo(change_set_id)

    assert target.is_dir()
    pending = await store.list_pending()
    assert len(pending) == 1
    committed = await store.get(change_set_id)
    assert committed is not None
    assert committed.lifecycle is ChangeLifecycle.UNDONE

    monkeypatch.setattr(store, "delete_pending", original_delete_pending)
    await journal.reconcile_pending()

    assert target.is_dir()
    assert await store.list_pending() == []


async def test_undo_conflict_changes_nothing_and_preserves_lifecycle(
    operations_fixture: OperationsFixture,
) -> None:
    operations, change_set_id, workspace, store, _ = await create_two_file_change(
        operations_fixture
    )
    (workspace / "second.txt").write_bytes(b"user edit\n")

    with pytest.raises(ChangeConflict):
        await operations.undo(change_set_id)

    assert (workspace / "first.txt").read_bytes() == b"after first\n"
    assert (workspace / "second.txt").read_bytes() == b"user edit\n"
    change_set = await store.get(change_set_id)
    assert change_set is not None
    assert change_set.lifecycle is ChangeLifecycle.APPLIED


async def test_undo_binds_parent_identity_across_preflight_and_restore(
    operations_fixture: OperationsFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations, change_set_id, workspace, store, _ = await create_nested_file_change(
        operations_fixture
    )
    parent = workspace / "parent"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "file.txt"
    sentinel.write_bytes(b"after")
    replacement = ParentReplacement(parent, outside)
    original_save_pending = store.save_pending

    async def save_pending_with_race(pending: PendingMutation) -> None:
        await original_save_pending(pending)
        replacement.trigger()

    monkeypatch.setattr(store, "save_pending", save_pending_with_race)
    error: ChangeConflict | None = None
    try:
        try:
            await operations.undo(change_set_id)
        except ChangeConflict as caught:
            error = caught
    finally:
        replacement.restore()

    assert sentinel.read_bytes() == b"after"
    if replacement.replaced:
        assert error is not None
        assert (parent / "file.txt").read_bytes() == b"after"
        change_set = await store.get(change_set_id)
        assert change_set is not None
        assert change_set.lifecycle is ChangeLifecycle.APPLIED
        pending_items = await store.list_pending()
        assert len(pending_items) == 1
        assert pending_items[0].id.startswith("undo_")
    else:
        assert os.name == "nt"
        assert error is None
        assert (parent / "file.txt").read_bytes() == b"before"
        assert await store.list_pending() == []
        change_set = await store.get(change_set_id)
        assert change_set is not None
        assert change_set.lifecycle is ChangeLifecycle.UNDONE


async def test_undo_rejects_hard_link_without_touching_other_name(
    operations_fixture: OperationsFixture,
    tmp_path: Path,
) -> None:
    operations, change_set_id, workspace, store, _ = await create_nested_file_change(
        operations_fixture
    )
    path = workspace / "parent" / "file.txt"
    sentinel = tmp_path / "outside.txt"
    sentinel.write_bytes(b"after")
    path.unlink()
    os.link(sentinel, path)

    with pytest.raises(ChangeConflict):
        await operations.undo(change_set_id)

    assert sentinel.read_bytes() == b"after"
    assert path.read_bytes() == b"after"
    change_set = await store.get(change_set_id)
    assert change_set is not None
    assert change_set.lifecycle is ChangeLifecycle.APPLIED


@pytest.mark.parametrize("rooted_path", [r"\outside.txt", "/outside.txt"])
async def test_undo_rejects_rooted_change_paths(
    operations_fixture: OperationsFixture,
    monkeypatch: pytest.MonkeyPatch,
    rooted_path: str,
) -> None:
    operations, change_set_id, _, store, _ = await create_nested_file_change(
        operations_fixture
    )
    change_set = await store.get(change_set_id)
    assert change_set is not None
    await store.save(
        change_set.model_copy(
            update={
                "files": [change_set.files[0].model_copy(update={"path": rooted_path})]
            }
        )
    )
    monkeypatch.setattr(
        "awesome_agent.core.workspace.path_syntax.workspace_path_platform",
        lambda: "windows",
    )

    with pytest.raises(ChangeLifecycleError, match="escapes or aliases"):
        await operations.undo(change_set_id)


async def test_undo_rolls_back_prior_files_when_a_later_restore_fails(
    operations_fixture: OperationsFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations, change_set_id, workspace, store, _ = await create_two_file_change(
        operations_fixture
    )
    original_restore = WorkspaceTreeTransaction.restore
    calls = 0

    def fail_second_restore(
        self: WorkspaceTreeTransaction,
        target: BoundWorkspaceNode,
        desired: NodeSnapshot | None,
    ) -> NodeSnapshot | None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated restore failure")
        return original_restore(self, target, desired)

    monkeypatch.setattr(WorkspaceTreeTransaction, "restore", fail_second_restore)

    with pytest.raises(RuntimeError, match="simulated restore failure"):
        await operations.undo(change_set_id)

    assert (workspace / "first.txt").read_bytes() == b"after first\n"
    assert (workspace / "second.txt").read_bytes() == b"after second\n"
    assert await store.list_pending() == []
    change_set = await store.get(change_set_id)
    assert change_set is not None
    assert change_set.lifecycle is ChangeLifecycle.APPLIED


async def test_reconcile_recovers_all_files_after_process_interruption(
    operations_fixture: OperationsFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SimulatedProcessInterruption(BaseException):
        pass

    operations, change_set_id, workspace, store, blobs = await create_two_file_change(
        operations_fixture
    )
    original_restore = WorkspaceTreeTransaction.restore
    calls = 0

    def interrupt_second_restore(
        self: WorkspaceTreeTransaction,
        target: BoundWorkspaceNode,
        desired: NodeSnapshot | None,
    ) -> NodeSnapshot | None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SimulatedProcessInterruption
        return original_restore(self, target, desired)

    monkeypatch.setattr(WorkspaceTreeTransaction, "restore", interrupt_second_restore)
    with pytest.raises(SimulatedProcessInterruption):
        await operations.undo(change_set_id)

    assert len(await store.list_pending()) == 2
    await ChangeJournal(store, blobs, resolve_workspace(workspace)).reconcile_pending()

    assert (workspace / "first.txt").read_bytes() == b"after first\n"
    assert (workspace / "second.txt").read_bytes() == b"after second\n"
    assert await store.list_pending() == []
    change_set = await store.get(change_set_id)
    assert change_set is not None
    assert change_set.lifecycle is ChangeLifecycle.APPLIED


async def test_partial_undo_warns_and_execute_only_is_not_reversible(
    operations_fixture: OperationsFixture,
) -> None:
    journal, operations, _, _, workspace = operations_fixture
    path = workspace / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    mixed = await journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    await journal.apply_file_mutation(
        change_set_id=mixed.id,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
        target=bound_path_mutation(
            workspace,
            path,
            partial(write_bytes, path, b"after"),
        ),
    )
    await journal.record_execute(
        change_set_id=mixed.id,
        command="pytest",
        observed_paths=[],
    )
    assert (await journal.seal(mixed.id)).reversibility is ChangeReversibility.PARTIAL

    result = await operations.undo(mixed.id)

    assert result.warning is not None
    assert result.unmanaged_effects_restored is False

    execute_only = await journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    await journal.record_execute(
        change_set_id=execute_only.id,
        command="pytest",
        observed_paths=[],
    )
    await journal.seal(execute_only.id)
    with pytest.raises(ChangeNotReversible):
        await operations.undo(execute_only.id)


async def test_undo_rejects_replacement_of_bound_workspace_root(
    operations_fixture: OperationsFixture,
    tmp_path: Path,
) -> None:
    journal, operations, _, _, workspace = operations_fixture
    path = workspace / "file.txt"
    path.write_bytes(b"before")
    mode = stat.S_IMODE(path.stat().st_mode)
    change_set = await journal.begin(
        session_id="session_1",
        turn_id=None,
        workspace=resolve_workspace(workspace),
    )
    await journal.apply_file_mutation(
        change_set_id=change_set.id,
        kind=FileChangeKind.UPDATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"after", mode),
        target=bound_path_mutation(
            workspace,
            path,
            partial(write_bytes, path, b"after"),
        ),
    )
    await journal.seal(change_set.id)
    original = tmp_path / "workspace-original"
    workspace.rename(original)
    workspace.mkdir()
    replacement = workspace / "file.txt"
    replacement.write_bytes(b"after")

    with pytest.raises(ChangeConflict):
        await operations.undo(change_set.id)

    assert replacement.read_bytes() == b"after"
