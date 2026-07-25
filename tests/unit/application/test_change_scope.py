import hashlib
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from awesome_agent.application.change_scope import ChangeScope
from awesome_agent.core.changes import (
    BoundFileMutation,
    ChangeJournal,
    ChangeLifecycle,
    FileChangeKind,
    FileNodeType,
    NodeSnapshot,
)
from awesome_agent.core.changes.errors import (
    ChangeLifecycleError,
    PendingMutationConflict,
)
from awesome_agent.core.changes.ports import PendingMutation
from awesome_agent.core.tools import ToolExecutionContext, ToolOutput, ToolSpec
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore


class _Arguments(BaseModel):
    pass


async def _handler(
    arguments: BaseModel,
    context: ToolExecutionContext,
) -> ToolOutput:
    del arguments, context
    return ToolOutput(content="ok")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name, read_only in (
        ("read_file", True),
        ("write_file", False),
        ("edit_file", False),
    ):
        registry.register(
            spec=ToolSpec(
                name=name,
                description=f"{name} test tool",
                input_schema=_Arguments.model_json_schema(),
                capability="workspace.read" if read_only else "workspace.write",
                read_only=read_only,
            ),
            input_model=_Arguments,
            handler=_handler,
        )
    return registry


def _scope(
    tmp_path: Path,
) -> tuple[ChangeScope, SQLiteChangeSetStore, str]:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir(exist_ok=True)
    workspace = resolve_workspace(workspace_path)
    store = SQLiteChangeSetStore(tmp_path / "application.db")
    journal = ChangeJournal(
        store,
        FileChangeBlobStore(tmp_path / "change-journal"),
        workspace,
    )
    return (
        ChangeScope(
            journal=journal,
            store=store,
            registry=_registry(),
            session_id="session_1",
            workspace=workspace,
        ),
        store,
        workspace.key,
    )


def test_read_only_tool_does_not_allocate_change_set(tmp_path: Path) -> None:
    scope, store, workspace_key = _scope(tmp_path)

    assert (
        scope.change_set_for_tool(
            tool_name="read_file",
            owner="turn_1",
            turn_id="turn_1",
        )
        is None
    )
    assert store.latest(workspace_key) is None


def test_unknown_tool_does_not_allocate_change_set(tmp_path: Path) -> None:
    scope, store, workspace_key = _scope(tmp_path)

    assert (
        scope.change_set_for_tool(
            tool_name="unknown_tool",
            owner="turn_1",
            turn_id="turn_1",
        )
        is None
    )
    assert store.latest(workspace_key) is None


def test_write_tools_reuse_one_owner_change_set(tmp_path: Path) -> None:
    scope, store, workspace_key = _scope(tmp_path)

    first = scope.change_set_for_tool(
        tool_name="write_file",
        owner="turn_1",
        turn_id="turn_1",
    )
    second = scope.change_set_for_tool(
        tool_name="edit_file",
        owner="turn_1",
        turn_id="turn_1",
    )

    assert first is not None
    assert second == first
    assert store.latest(workspace_key) is not None


def test_sealing_without_a_mutating_tool_is_a_noop(tmp_path: Path) -> None:
    scope, store, workspace_key = _scope(tmp_path)

    scope.seal("turn_1")

    assert store.latest(workspace_key) is None


def _conflicting_pending(
    tmp_path: Path,
    *,
    store: SQLiteChangeSetStore,
    change_set_id: str,
    workspace_key: str,
    pending_id: str,
    relative_path: str,
) -> PendingMutation:
    path = tmp_path / "workspace" / relative_path
    path.write_bytes(b"unexpected")
    mode = stat.S_IMODE(path.stat().st_mode)
    blobs = FileChangeBlobStore(tmp_path / "change-journal")
    pending = PendingMutation(
        id=pending_id,
        change_set_id=change_set_id,
        workspace_key=workspace_key,
        relative_path=relative_path,
        kind=FileChangeKind.UPDATED,
        node_type=FileNodeType.FILE,
        before_hash=hashlib.sha256(b"before").hexdigest(),
        before_blob=blobs.put(b"before"),
        before_mode=mode,
        intended_after_hash=hashlib.sha256(b"after").hexdigest(),
        intended_after_blob=blobs.put(b"after"),
        intended_after_mode=mode,
        created_at=datetime.now(UTC),
    )
    store.save_pending(pending)
    return pending


def test_seal_ignores_a_conflicting_pending_from_another_change_set(
    tmp_path: Path,
) -> None:
    scope, store, workspace_key = _scope(tmp_path)
    current_id = scope.acquire("turn_current", turn_id="turn_current")
    current = store.get(current_id)
    assert current is not None
    foreign = current.model_copy(
        update={
            "id": "change_foreign",
            "turn_id": "turn_foreign",
            "created_at": datetime.now(UTC),
        }
    )
    store.save(foreign)
    pending = _conflicting_pending(
        tmp_path,
        store=store,
        change_set_id=foreign.id,
        workspace_key=workspace_key,
        pending_id="pending_foreign",
        relative_path="foreign.txt",
    )

    scope.seal("turn_current")

    sealed = store.get(current_id)
    assert sealed is not None
    assert sealed.lifecycle is ChangeLifecycle.APPLIED
    retained = store.get(foreign.id)
    assert retained is not None
    assert retained.lifecycle is ChangeLifecycle.OPEN
    assert store.list_pending() == [pending]


def test_failed_seal_retains_the_owner_for_a_safe_retry(tmp_path: Path) -> None:
    scope, store, workspace_key = _scope(tmp_path)
    change_set_id = scope.acquire("turn_1", turn_id="turn_1")
    _conflicting_pending(
        tmp_path,
        store=store,
        change_set_id=change_set_id,
        workspace_key=workspace_key,
        pending_id="pending_current",
        relative_path="current.txt",
    )

    with pytest.raises(PendingMutationConflict):
        scope.seal("turn_1")

    (tmp_path / "workspace" / "current.txt").write_bytes(b"after")
    scope.seal("turn_1")

    sealed = store.get(change_set_id)
    assert sealed is not None
    assert sealed.lifecycle is ChangeLifecycle.APPLIED
    assert len(sealed.files) == 1
    assert store.list_pending() == []


def test_startup_reconcile_seals_an_orphaned_open_change_set(
    tmp_path: Path,
) -> None:
    scope, store, workspace_key = _scope(tmp_path)
    orphaned_id = scope.acquire("turn_1", turn_id="turn_1")
    workspace_path = tmp_path / "workspace"
    path = workspace_path / "file.txt"

    def mutate() -> None:
        path.write_bytes(b"after")

    def capture_after() -> NodeSnapshot:
        return NodeSnapshot(FileNodeType.FILE, path.read_bytes(), None)

    journal = ChangeJournal(
        store,
        FileChangeBlobStore(tmp_path / "change-journal"),
        resolve_workspace(workspace_path),
    )
    journal.apply_file_mutation(
        change_set_id=orphaned_id,
        kind=FileChangeKind.CREATED,
        intended_after=NodeSnapshot(FileNodeType.FILE, b"after", None),
        target=BoundFileMutation(
            relative_path="file.txt",
            before=None,
            mutate=mutate,
            capture_after=capture_after,
        ),
    )
    orphaned = store.get(orphaned_id)
    assert orphaned is not None
    assert orphaned.lifecycle is ChangeLifecycle.OPEN
    assert len(orphaned.files) == 1
    assert store.list_pending() == []

    restarted_scope, restarted_store, restarted_workspace_key = _scope(tmp_path)
    assert restarted_workspace_key == workspace_key
    restarted_scope.reconcile()

    recovered = restarted_store.get(orphaned_id)
    assert recovered is not None
    assert recovered.lifecycle is ChangeLifecycle.APPLIED
    assert recovered.sealed_at is not None


def test_reconcile_does_not_seal_the_active_scope_change_set(
    tmp_path: Path,
) -> None:
    scope, store, _ = _scope(tmp_path)
    active_id = scope.acquire("turn_1", turn_id="turn_1")

    with pytest.raises(ChangeLifecycleError, match="active ChangeSets"):
        scope.reconcile()

    active = store.get(active_id)
    assert active is not None
    assert active.lifecycle is ChangeLifecycle.OPEN


def test_reconcile_does_not_seal_another_workspaces_open_change_set(
    tmp_path: Path,
) -> None:
    scope, store, _ = _scope(tmp_path)
    foreign = store.get(scope.acquire("turn_1", turn_id="turn_1"))
    assert foreign is not None
    foreign = foreign.model_copy(
        update={"id": "change_foreign", "workspace_key": "ws_foreign"}
    )
    store.save(foreign)

    restarted_scope, restarted_store, _ = _scope(tmp_path)
    restarted_scope.reconcile()

    retained = restarted_store.get(foreign.id)
    assert retained is not None
    assert retained.lifecycle is ChangeLifecycle.OPEN
