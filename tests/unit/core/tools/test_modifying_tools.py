from pathlib import Path

import pytest

from awesome_agent.core.changes import ChangeJournal
from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools import (
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutor,
    ToolInvariantError,
    ToolRequest,
    ToolStatus,
)
from awesome_agent.core.tools.builtins import register_modifying_tools
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore


def modifying_fixture(
    tmp_path: Path,
    *,
    with_change_set: bool = True,
) -> tuple[ToolExecutor, ToolExecutionContext, ChangeJournal, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)
    journal = ChangeJournal(
        SQLiteChangeSetStore(tmp_path / "application.db"),
        FileChangeBlobStore(tmp_path / "change-journal"),
        identity,
    )
    registry = ToolRegistry()
    register_modifying_tools(registry, journal)
    change_set_id = None
    if with_change_set:
        change_set_id = journal.begin(
            session_id="session_1",
            turn_id="turn_1",
            workspace=identity,
        ).id
    context = ToolExecutionContext(
        workspace=identity,
        operation_id="operation_1",
        turn_id="turn_1",
        emitter=EventEmitter(session_id="session_1", sink=CollectingEventSink()),
        change_set_id=change_set_id,
    )
    return ToolExecutor(registry), context, journal, workspace


@pytest.mark.asyncio
async def test_write_file_creates_and_overwrites_utf8_content(tmp_path: Path) -> None:
    executor, context, journal, workspace = modifying_fixture(tmp_path)

    created = await executor.execute(
        ToolRequest(
            call_id="call_1",
            tool_name="write_file",
            arguments={"path": "notes.txt", "content": "first"},
        ),
        context=context,
    )
    overwritten = await executor.execute(
        ToolRequest(
            call_id="call_2",
            tool_name="write_file",
            arguments={"path": "notes.txt", "content": "second"},
        ),
        context=context,
    )

    assert created.status is ToolStatus.SUCCESS
    assert overwritten.status is ToolStatus.SUCCESS
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "second"
    change_set = journal.seal(context.change_set_id or "")
    assert len(change_set.files) == 2


@pytest.mark.asyncio
async def test_edit_file_replaces_one_exact_occurrence(tmp_path: Path) -> None:
    executor, context, journal, workspace = modifying_fixture(tmp_path)
    path = workspace / "app.py"
    path.write_text("before\n", encoding="utf-8")

    result = await executor.execute(
        ToolRequest(
            call_id="call_1",
            tool_name="edit_file",
            arguments={
                "path": "app.py",
                "old_string": "before",
                "new_string": "after",
            },
        ),
        context=context,
    )

    assert result.status is ToolStatus.SUCCESS
    assert path.read_text(encoding="utf-8") == "after\n"
    change_set = journal.seal(context.change_set_id or "")
    assert len(change_set.files) == 1


@pytest.mark.asyncio
async def test_edit_file_rejects_ambiguous_replacement(tmp_path: Path) -> None:
    executor, context, _, workspace = modifying_fixture(tmp_path)
    path = workspace / "app.py"
    path.write_text("same same", encoding="utf-8")

    result = await executor.execute(
        ToolRequest(
            call_id="call_1",
            tool_name="edit_file",
            arguments={
                "path": "app.py",
                "old_string": "same",
                "new_string": "changed",
            },
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.CONFLICT
    assert path.read_text(encoding="utf-8") == "same same"


@pytest.mark.asyncio
async def test_missing_change_set_is_invariant_failure_before_write(
    tmp_path: Path,
) -> None:
    executor, context, _, workspace = modifying_fixture(
        tmp_path,
        with_change_set=False,
    )

    with pytest.raises(ToolInvariantError):
        await executor.execute(
            ToolRequest(
                call_id="call_1",
                tool_name="write_file",
                arguments={"path": "notes.txt", "content": "content"},
            ),
            context=context,
        )

    assert not (workspace / "notes.txt").exists()
