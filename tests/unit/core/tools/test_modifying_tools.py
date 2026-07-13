from dataclasses import replace
from pathlib import Path
from time import monotonic
from unittest.mock import Mock

import pytest

import awesome_agent.core.changes.journal as journal_module
from awesome_agent.core.changes import ChangeJournal
from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools import (
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolExecutor,
    ToolInvariantError,
    ToolRequest,
    ToolStatus,
)
from awesome_agent.core.tools.builtins import register_modifying_tools
from awesome_agent.core.tools.permissions import (
    PermissionMode,
    PermissionSession,
    ToolApprovalDecision,
    ToolApprovalRequest,
)
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore


def modifying_fixture(
    tmp_path: Path,
    *,
    with_change_set: bool = True,
) -> tuple[
    ToolExecutor,
    ToolExecutionContext,
    ChangeJournal,
    Path,
    CollectingEventSink,
]:
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
    sink = CollectingEventSink()
    context = ToolExecutionContext(
        workspace=identity,
        thread_id="thread_1",
        operation_id="operation_1",
        turn_id="turn_1",
        origin=ToolExecutionOrigin.AGENT,
        emitter=EventEmitter(
            session_id="session_1",
            workspace_key=identity.key,
            sink=sink,
        ),
        activity_writer=Mock(),
        monotonic=monotonic,
        change_set_id=change_set_id,
        permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
    )
    return ToolExecutor(registry), context, journal, workspace, sink


@pytest.mark.asyncio
async def test_write_file_approval_describes_the_real_target(tmp_path: Path) -> None:
    executor, context, _, workspace, _ = modifying_fixture(tmp_path)
    approvals: list[ToolApprovalRequest] = []

    async def approve(request: ToolApprovalRequest) -> ToolApprovalDecision:
        assert not (workspace / "circle_area.py").exists()
        approvals.append(request)
        return ToolApprovalDecision.ALLOW_ONCE

    context = replace(
        context,
        permission_session=PermissionSession(),
        approval_resolver=approve,
    )
    result = await executor.execute(
        ToolRequest(
            call_id="call_write",
            tool_name="write_file",
            arguments={"path": "circle_area.py", "content": "pass\n"},
        ),
        context=context,
    )

    assert result.status is ToolStatus.SUCCESS
    assert approvals == [
        ToolApprovalRequest(
            capability="workspace.write",
            operation="create",
            target="circle_area.py",
            prompt="Do you want to create circle_area.py?",
        )
    ]


@pytest.mark.asyncio
async def test_write_file_creates_and_overwrites_utf8_content(tmp_path: Path) -> None:
    executor, context, journal, workspace, sink = modifying_fixture(tmp_path)

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

    assert created.presentation is not None
    assert created.presentation.model_dump() == {
        "verb": "Write",
        "target": "notes.txt",
        "outcome": "Created",
        "summary": "1 line",
        "detail": None,
        "detail_truncated_count": None,
        "duration_ms": sink.events[1].payload.duration_ms,  # type: ignore[union-attr]
    }
    assert overwritten.presentation is not None
    assert overwritten.presentation.outcome == "Updated"

    assert created.status is ToolStatus.SUCCESS
    assert overwritten.status is ToolStatus.SUCCESS
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "second"
    change_set = journal.seal(context.change_set_id or "")
    assert len(change_set.files) == 2


@pytest.mark.asyncio
async def test_edit_file_replaces_one_exact_occurrence(tmp_path: Path) -> None:
    executor, context, journal, workspace, _ = modifying_fixture(tmp_path)
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
    executor, context, _, workspace, _ = modifying_fixture(tmp_path)
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
    executor, context, _, workspace, _ = modifying_fixture(
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


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [".", ".git", ".env"])
async def test_delete_rejects_root_git_and_sensitive_targets(
    tmp_path: Path,
    target: str,
) -> None:
    executor, context, _, workspace, _ = modifying_fixture(tmp_path)
    (workspace / ".git").mkdir()
    (workspace / ".env").write_text("SECRET=value", encoding="utf-8")

    result = await executor.execute(
        ToolRequest(
            call_id="call_delete",
            tool_name="delete",
            arguments={"path": target},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED
    assert workspace.exists()
    assert (workspace / ".git").exists()
    assert (workspace / ".env").exists()


@pytest.mark.asyncio
async def test_delete_capacity_fails_before_removing_any_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, context, _, workspace, _ = modifying_fixture(tmp_path)
    target = workspace / "target"
    target.mkdir()
    (target / "one.txt").write_text("one", encoding="utf-8")
    (target / "two.txt").write_text("two", encoding="utf-8")
    monkeypatch.setattr(journal_module, "MAX_CHANGESET_FILES", 1)

    result = await executor.execute(
        ToolRequest(
            call_id="call_delete",
            tool_name="delete",
            arguments={"path": "target"},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.EXECUTION_FAILED
    assert (target / "one.txt").exists()
    assert (target / "two.txt").exists()
