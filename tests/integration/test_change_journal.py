import os
from collections.abc import AsyncIterator
from pathlib import Path
from time import monotonic
from unittest.mock import AsyncMock

import pytest

from awesome_agent.core.changes import ChangeJournal, ChangeOperations, FileNodeType
from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools import (
    PermissionMode,
    PermissionSession,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolExecutor,
    ToolRequest,
    ToolStatus,
)
from awesome_agent.core.tools.builtins import register_modifying_tools
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage.application_sqlite import ApplicationSQLite
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore


@pytest.fixture
async def application_database(tmp_path: Path) -> AsyncIterator[ApplicationSQLite]:
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.aclose()


async def delete_fixture(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> tuple[
    ToolExecutor,
    ToolExecutionContext,
    ChangeJournal,
    FileChangeBlobStore,
    Path,
]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)
    blobs = FileChangeBlobStore(tmp_path / "change-journal")
    journal = ChangeJournal(
        SQLiteChangeSetStore(application_database),
        blobs,
        identity,
    )
    change_set = await journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=identity,
    )
    registry = ToolRegistry()
    register_modifying_tools(registry, journal, workspace=identity)
    context = ToolExecutionContext(
        workspace=identity,
        thread_id="thread_1",
        operation_id="operation_1",
        turn_id="turn_1",
        origin=ToolExecutionOrigin.AGENT,
        emitter=EventEmitter(
            session_id="session_1",
            workspace_key=identity.key,
            sink=CollectingEventSink(),
        ),
        activity_writer=AsyncMock(),
        monotonic=monotonic,
        change_set_id=change_set.id,
        permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
    )
    return ToolExecutor(registry), context, journal, blobs, workspace


@pytest.mark.asyncio
async def test_recursive_delete_records_every_node_for_restore(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    executor, context, journal, blobs, workspace = await delete_fixture(
        tmp_path,
        application_database,
    )
    target = workspace / "target"
    nested = target / "nested"
    empty = target / "empty"
    nested.mkdir(parents=True)
    empty.mkdir()
    (target / "text.txt").write_text("text", encoding="utf-8")
    (nested / "binary.bin").write_bytes(b"before\x00after")

    result = await executor.execute(
        ToolRequest(
            call_id="call_delete",
            tool_name="delete",
            arguments={"path": "target"},
        ),
        context=context,
    )
    change_set = await journal.seal(context.change_set_id or "")

    assert result.status is ToolStatus.SUCCESS
    assert not target.exists()
    assert {change.path for change in change_set.files} == {
        "target",
        "target/empty",
        "target/nested",
        "target/nested/binary.bin",
        "target/text.txt",
    }
    binary = next(
        change for change in change_set.files if change.path.endswith("binary.bin")
    )
    assert binary.node_type is FileNodeType.FILE
    assert binary.before_blob is not None
    assert blobs.get(binary.before_blob) == b"before\x00after"
    assert all(change.before_mode is not None for change in change_set.files)

    reopened = ChangeOperations(
        SQLiteChangeSetStore(application_database),
        FileChangeBlobStore(tmp_path / "change-journal"),
        resolve_workspace(workspace),
    )
    await reopened.undo(change_set.id)

    assert (target / "text.txt").read_text(encoding="utf-8") == "text"
    assert (nested / "binary.bin").read_bytes() == b"before\x00after"
    assert empty.is_dir()

    await reopened.redo(change_set.id)
    assert not target.exists()


@pytest.mark.asyncio
async def test_delete_removes_symlink_without_following_target(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    executor, context, journal, blobs, workspace = await delete_fixture(
        tmp_path,
        application_database,
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = workspace / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("File symlinks are not available on this platform.")
    raw_target = os.readlink(link)

    result = await executor.execute(
        ToolRequest(
            call_id="call_delete",
            tool_name="delete",
            arguments={"path": "linked.txt"},
        ),
        context=context,
    )
    change_set = await journal.seal(context.change_set_id or "")

    assert result.status is ToolStatus.SUCCESS
    assert not link.exists()
    assert outside.read_text(encoding="utf-8") == "outside"
    assert len(change_set.files) == 1
    change = change_set.files[0]
    assert change.node_type is FileNodeType.SYMLINK
    assert change.before_blob is not None
    assert blobs.get(change.before_blob) == os.fsencode(raw_target)

    reopened = ChangeOperations(
        SQLiteChangeSetStore(application_database),
        FileChangeBlobStore(tmp_path / "change-journal"),
        resolve_workspace(workspace),
    )
    await reopened.undo(change_set.id)
    assert link.is_symlink()
    assert os.readlink(link) == raw_target
    assert link.read_text(encoding="utf-8") == "outside"
    await reopened.redo(change_set.id)
    assert not link.exists()
    assert outside.read_text(encoding="utf-8") == "outside"
