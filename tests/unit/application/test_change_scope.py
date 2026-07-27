import asyncio
import hashlib
import stat
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from awesome_agent.application.change_scope import ChangeScope
from awesome_agent.core.changes import (
    BoundFileMutation,
    ChangeJournal,
    ChangeLifecycle,
    ChangeSet,
    ExecuteObservation,
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
from awesome_agent.core.workspace import WorkspaceIdentity, resolve_workspace
from awesome_agent.storage import ApplicationSQLite
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore


class _Arguments(BaseModel):
    pass


class _BlockingBeginJournal(ChangeJournal):
    def __init__(
        self,
        store: SQLiteChangeSetStore,
        blobs: FileChangeBlobStore,
        workspace: WorkspaceIdentity,
    ) -> None:
        super().__init__(store, blobs, workspace)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def begin(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        workspace: WorkspaceIdentity,
    ) -> ChangeSet:
        change_set = await super().begin(
            session_id=session_id,
            turn_id=turn_id,
            workspace=workspace,
        )
        self.entered.set()
        await self.release.wait()
        return change_set


class _BlockingSealJournal(ChangeJournal):
    def __init__(
        self,
        store: SQLiteChangeSetStore,
        blobs: FileChangeBlobStore,
        workspace: WorkspaceIdentity,
    ) -> None:
        super().__init__(store, blobs, workspace)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def seal(self, change_set_id: str) -> ChangeSet:
        change_set = await super().seal(change_set_id)
        self.entered.set()
        await self.release.wait()
        return change_set


class _BlockingFailBeginJournal(ChangeJournal):
    def __init__(
        self,
        store: SQLiteChangeSetStore,
        blobs: FileChangeBlobStore,
        workspace: WorkspaceIdentity,
    ) -> None:
        super().__init__(store, blobs, workspace)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def begin(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        workspace: WorkspaceIdentity,
    ) -> ChangeSet:
        del session_id, turn_id, workspace
        self.entered.set()
        await self.release.wait()
        raise RuntimeError("journal begin failed")


class _RefusingDeleteChangeSetStore(SQLiteChangeSetStore):
    def __init__(self, database: ApplicationSQLite) -> None:
        super().__init__(database)
        self.delete_attempts: list[str] = []

    async def delete_empty_open(self, change_set_id: str) -> bool:
        self.delete_attempts.append(change_set_id)
        return False


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
    database: ApplicationSQLite,
) -> tuple[ChangeScope, SQLiteChangeSetStore, str]:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir(exist_ok=True)
    workspace = resolve_workspace(workspace_path)
    store = SQLiteChangeSetStore(database)
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


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[ApplicationSQLite]:
    owner = ApplicationSQLite(tmp_path / "application.db")
    await owner.initialize()
    try:
        yield owner
    finally:
        await owner.aclose()


@pytest.mark.asyncio
async def test_read_only_tool_does_not_allocate_change_set(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    scope, store, workspace_key = _scope(tmp_path, database)

    assert (
        await scope.change_set_for_tool(
            tool_name="read_file",
            owner="turn_1",
            turn_id="turn_1",
        )
        is None
    )
    assert await store.latest(workspace_key) is None


@pytest.mark.asyncio
async def test_unknown_tool_does_not_allocate_change_set(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    scope, store, workspace_key = _scope(tmp_path, database)

    assert (
        await scope.change_set_for_tool(
            tool_name="unknown_tool",
            owner="turn_1",
            turn_id="turn_1",
        )
        is None
    )
    assert await store.latest(workspace_key) is None


@pytest.mark.asyncio
async def test_write_tools_reuse_one_owner_change_set(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    scope, store, workspace_key = _scope(tmp_path, database)

    first = await scope.change_set_for_tool(
        tool_name="write_file",
        owner="turn_1",
        turn_id="turn_1",
    )
    second = await scope.change_set_for_tool(
        tool_name="edit_file",
        owner="turn_1",
        turn_id="turn_1",
    )

    assert first is not None
    assert second == first
    assert await store.latest(workspace_key) is not None


@pytest.mark.asyncio
async def test_sealing_without_a_mutating_tool_is_a_noop(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    scope, store, workspace_key = _scope(tmp_path, database)

    await scope.seal("turn_1")

    assert await store.latest(workspace_key) is None


@pytest.mark.asyncio
async def test_failed_finalization_deletes_an_empty_open_change_set(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    scope, store, _ = _scope(tmp_path, database)
    first = await scope.acquire("export_1", turn_id=None)

    await scope.finalize_failed("export_1")

    assert await store.get(first) is None
    assert await scope.acquire("export_1", turn_id=None) != first


@pytest.mark.asyncio
async def test_failed_finalization_seals_an_open_change_set_with_evidence(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    scope, store, _ = _scope(tmp_path, database)
    first = await scope.acquire("export_1", turn_id=None)
    open_change = await store.get(first)
    assert open_change is not None
    await store.save(
        open_change.model_copy(
            update={
                "execute": [
                    ExecuteObservation(command="export", observed_paths=["out.md"])
                ]
            }
        )
    )

    await scope.finalize_failed("export_1")

    sealed = await store.get(first)
    assert sealed is not None
    assert sealed.lifecycle is ChangeLifecycle.APPLIED
    assert sealed.sealed_at is not None
    assert await scope.acquire("export_1", turn_id=None) != first


@pytest.mark.asyncio
async def test_failed_finalization_seals_when_atomic_empty_delete_is_refused(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    store = _RefusingDeleteChangeSetStore(database)
    scope = ChangeScope(
        journal=ChangeJournal(
            store,
            FileChangeBlobStore(tmp_path / "change-journal"),
            workspace,
        ),
        store=store,
        registry=_registry(),
        session_id="session_1",
        workspace=workspace,
    )
    first = await scope.acquire("export_1", turn_id=None)

    await scope.finalize_failed("export_1")

    retained = await store.get(first)
    assert store.delete_attempts == [first]
    assert retained is not None
    assert retained.lifecycle is ChangeLifecycle.APPLIED
    assert retained.sealed_at is not None
    assert await scope.acquire("export_1", turn_id=None) != first


async def _conflicting_pending(
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
    await store.save_pending(pending)
    return pending


@pytest.mark.asyncio
async def test_seal_ignores_a_conflicting_pending_from_another_change_set(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    scope, store, workspace_key = _scope(tmp_path, database)
    current_id = await scope.acquire("turn_current", turn_id="turn_current")
    current = await store.get(current_id)
    assert current is not None
    foreign = current.model_copy(
        update={
            "id": "change_foreign",
            "turn_id": "turn_foreign",
            "created_at": datetime.now(UTC),
        }
    )
    await store.save(foreign)
    pending = await _conflicting_pending(
        tmp_path,
        store=store,
        change_set_id=foreign.id,
        workspace_key=workspace_key,
        pending_id="pending_foreign",
        relative_path="foreign.txt",
    )

    await scope.seal("turn_current")

    sealed = await store.get(current_id)
    assert sealed is not None
    assert sealed.lifecycle is ChangeLifecycle.APPLIED
    retained = await store.get(foreign.id)
    assert retained is not None
    assert retained.lifecycle is ChangeLifecycle.OPEN
    assert await store.list_pending() == [pending]


@pytest.mark.asyncio
async def test_failed_seal_retains_the_owner_for_a_safe_retry(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    scope, store, workspace_key = _scope(tmp_path, database)
    change_set_id = await scope.acquire("turn_1", turn_id="turn_1")
    await _conflicting_pending(
        tmp_path,
        store=store,
        change_set_id=change_set_id,
        workspace_key=workspace_key,
        pending_id="pending_current",
        relative_path="current.txt",
    )

    with pytest.raises(PendingMutationConflict):
        await scope.seal("turn_1")

    (tmp_path / "workspace" / "current.txt").write_bytes(b"after")
    await scope.seal("turn_1")

    sealed = await store.get(change_set_id)
    assert sealed is not None
    assert sealed.lifecycle is ChangeLifecycle.APPLIED
    assert len(sealed.files) == 1
    assert await store.list_pending() == []


@pytest.mark.asyncio
async def test_failed_finalization_unpublishes_after_reconciliation_failure(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    scope, store, workspace_key = _scope(tmp_path, database)
    first = await scope.acquire("export_1", turn_id=None)
    await _conflicting_pending(
        tmp_path,
        store=store,
        change_set_id=first,
        workspace_key=workspace_key,
        pending_id="pending_export",
        relative_path="export.md",
    )

    with pytest.raises(PendingMutationConflict):
        await scope.finalize_failed("export_1")

    retained = await store.get(first)
    assert retained is not None
    assert retained.lifecycle is ChangeLifecycle.OPEN
    assert await scope.acquire("export_1", turn_id=None) != first


@pytest.mark.asyncio
async def test_startup_reconcile_seals_an_orphaned_open_change_set(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    scope, store, workspace_key = _scope(tmp_path, database)
    orphaned_id = await scope.acquire("turn_1", turn_id="turn_1")
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
    await journal.apply_file_mutation(
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
    orphaned = await store.get(orphaned_id)
    assert orphaned is not None
    assert orphaned.lifecycle is ChangeLifecycle.OPEN
    assert len(orphaned.files) == 1
    assert await store.list_pending() == []

    restarted_scope, restarted_store, restarted_workspace_key = _scope(
        tmp_path, database
    )
    assert restarted_workspace_key == workspace_key
    await restarted_scope.reconcile()

    recovered = await restarted_store.get(orphaned_id)
    assert recovered is not None
    assert recovered.lifecycle is ChangeLifecycle.APPLIED
    assert recovered.sealed_at is not None


@pytest.mark.asyncio
async def test_reconcile_does_not_seal_the_active_scope_change_set(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    scope, store, _ = _scope(tmp_path, database)
    active_id = await scope.acquire("turn_1", turn_id="turn_1")

    with pytest.raises(ChangeLifecycleError, match="active ChangeSets"):
        await scope.reconcile()

    active = await store.get(active_id)
    assert active is not None
    assert active.lifecycle is ChangeLifecycle.OPEN


@pytest.mark.asyncio
async def test_reconcile_does_not_seal_another_workspaces_open_change_set(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    scope, store, _ = _scope(tmp_path, database)
    foreign = await store.get(await scope.acquire("turn_1", turn_id="turn_1"))
    assert foreign is not None
    foreign = foreign.model_copy(
        update={"id": "change_foreign", "workspace_key": "ws_foreign"}
    )
    await store.save(foreign)

    restarted_scope, restarted_store, _ = _scope(tmp_path, database)
    await restarted_scope.reconcile()

    retained = await restarted_store.get(foreign.id)
    assert retained is not None
    assert retained.lifecycle is ChangeLifecycle.OPEN


@pytest.mark.asyncio
async def test_cancelled_acquire_publishes_the_durably_created_change_set(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    store = SQLiteChangeSetStore(database)
    journal = _BlockingBeginJournal(
        store,
        FileChangeBlobStore(tmp_path / "change-journal"),
        workspace,
    )
    scope = ChangeScope(
        journal=journal,
        store=store,
        registry=_registry(),
        session_id="session_1",
        workspace=workspace,
    )

    acquiring = asyncio.create_task(scope.acquire("turn_1", turn_id="turn_1"))
    await journal.entered.wait()
    acquiring.cancel("first cancellation")
    await asyncio.sleep(0)
    acquiring.cancel("second cancellation")
    journal.release.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await acquiring

    persisted = await store.latest(workspace.key)
    assert persisted is not None
    assert cancelled.value.args == ("first cancellation",)
    assert await scope.acquire("turn_1", turn_id="turn_1") == persisted.id
    assert len(await store.list_open(workspace.key)) == 1


@pytest.mark.asyncio
async def test_acquire_failure_after_cancellation_preserves_first_cancellation(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    store = SQLiteChangeSetStore(database)
    journal = _BlockingFailBeginJournal(
        store,
        FileChangeBlobStore(tmp_path / "change-journal"),
        workspace,
    )
    scope = ChangeScope(
        journal=journal,
        store=store,
        registry=_registry(),
        session_id="session_1",
        workspace=workspace,
    )

    acquiring = asyncio.create_task(scope.acquire("turn_1", turn_id="turn_1"))
    await journal.entered.wait()
    acquiring.cancel("first cancellation")
    await asyncio.sleep(0)
    acquiring.cancel("second cancellation")
    journal.release.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await acquiring

    assert cancelled.value.args == ("first cancellation",)
    assert await store.latest(workspace.key) is None


@pytest.mark.asyncio
async def test_cancelled_seal_unpublishes_the_durably_sealed_change_set(
    tmp_path: Path,
    database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    store = SQLiteChangeSetStore(database)
    journal = _BlockingSealJournal(
        store,
        FileChangeBlobStore(tmp_path / "change-journal"),
        workspace,
    )
    scope = ChangeScope(
        journal=journal,
        store=store,
        registry=_registry(),
        session_id="session_1",
        workspace=workspace,
    )
    first = await scope.acquire("turn_1", turn_id="turn_1")

    sealing = asyncio.create_task(scope.seal("turn_1"))
    await journal.entered.wait()
    sealing.cancel("first cancellation")
    await asyncio.sleep(0)
    sealing.cancel("second cancellation")
    journal.release.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await sealing

    sealed = await store.get(first)
    assert sealed is not None
    assert sealed.lifecycle is ChangeLifecycle.APPLIED
    assert cancelled.value.args == ("first cancellation",)
    assert await scope.acquire("turn_1", turn_id="turn_1") != first
