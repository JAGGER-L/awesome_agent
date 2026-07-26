import os
import subprocess
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from time import monotonic
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

import awesome_agent.core.changes.journal as journal_module
import awesome_agent.core.filesystem as core_filesystem_module
import awesome_agent.core.tools.filesystem as tools_filesystem_module
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
from awesome_agent.core.tools.filesystem import (
    SecureDeleteNode,
    WorkspaceDeleteTransaction,
)
from awesome_agent.core.tools.permissions import (
    PermissionMode,
    PermissionSession,
    ToolApprovalDecision,
    ToolApprovalRequest,
)
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage.application_sqlite import ApplicationSQLite
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore

delete_module = __import__(
    "awesome_agent.core.tools.builtins.delete",
    fromlist=["delete"],
)
edit_file_module = __import__(
    "awesome_agent.core.tools.builtins.edit_file",
    fromlist=["edit_file"],
)
write_file_module = __import__(
    "awesome_agent.core.tools.builtins.write_file",
    fromlist=["write_file"],
)
builtins_module = __import__(
    "awesome_agent.core.tools.builtins",
    fromlist=["resolve_workspace_path"],
)


@pytest.fixture
async def application_database(tmp_path: Path) -> AsyncIterator[ApplicationSQLite]:
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.aclose()


async def modifying_fixture(
    tmp_path: Path,
    application_database: ApplicationSQLite,
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
        SQLiteChangeSetStore(application_database),
        FileChangeBlobStore(tmp_path / "change-journal"),
        identity,
    )
    registry = ToolRegistry()
    register_modifying_tools(registry, journal, workspace=identity)
    change_set_id = None
    if with_change_set:
        change_set_id = (
            await journal.begin(
                session_id="session_1",
                turn_id="turn_1",
                workspace=identity,
            )
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
        activity_writer=AsyncMock(),
        monotonic=monotonic,
        change_set_id=change_set_id,
        permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
    )
    return ToolExecutor(registry), context, journal, workspace, sink


class _ParentReplacement:
    def __init__(self, parent: Path, outside: Path) -> None:
        self.parent = parent
        self.outside = outside
        self.original = parent.with_name(f"{parent.name}.original")
        self.replaced = False

    def trigger(self) -> None:
        try:
            self.parent.rename(self.original)
        except OSError:
            # Windows directory handles intentionally deny rename while pinned.
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


def _race_first_journal_mutation(
    monkeypatch: pytest.MonkeyPatch,
    journal: ChangeJournal,
    replacement: _ParentReplacement,
) -> None:
    original_apply = journal.apply_file_mutation
    raced = False

    def apply_with_race(*args: Any, **kwargs: Any) -> Any:
        nonlocal raced
        target = kwargs["target"]
        mutate = target.mutate

        def racing_mutate() -> None:
            nonlocal raced
            if not raced:
                raced = True
                replacement.trigger()
            mutate()

        return original_apply(
            *args,
            **{
                **kwargs,
                "target": replace(target, mutate=racing_mutate),
            },
        )

    monkeypatch.setattr(journal, "apply_file_mutation", apply_with_race)


@pytest.mark.asyncio
async def test_write_file_rejects_replacement_of_bound_workspace_root(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    executor, context, journal, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
    original = tmp_path / "workspace-original"
    workspace.rename(original)
    workspace.mkdir()

    result = await executor.execute(
        ToolRequest(
            call_id="call_replaced_workspace",
            tool_name="write_file",
            arguments={"path": "replacement.txt", "content": "do not write\n"},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.CONFLICT
    assert not (workspace / "replacement.txt").exists()
    assert (await journal.seal(context.change_set_id or "")).files == []


@pytest.mark.asyncio
async def test_write_file_rejects_target_created_after_missing_validation(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, context, journal, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
    target = workspace / "target.txt"
    original_resolve = write_file_module.resolve_workspace_path

    def resolve_then_create(*args: object, **kwargs: object) -> object:
        safe = original_resolve(*args, **kwargs)
        target.write_text("concurrent sentinel", encoding="utf-8")
        return safe

    monkeypatch.setattr(
        write_file_module,
        "resolve_workspace_path",
        resolve_then_create,
    )

    result = await executor.execute(
        ToolRequest(
            call_id="call_created_race",
            tool_name="write_file",
            arguments={"path": "target.txt", "content": "agent content"},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.CONFLICT
    assert target.read_text(encoding="utf-8") == "concurrent sentinel"
    assert (await journal.seal(context.change_set_id or "")).files == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "module"),
    [
        (
            "write_file",
            {"path": "target.txt", "content": "agent content"},
            write_file_module,
        ),
        (
            "edit_file",
            {
                "path": "target.txt",
                "old_string": "new generation sentinel",
                "new_string": "agent content",
            },
            edit_file_module,
        ),
    ],
)
async def test_write_and_edit_reject_regular_target_generation_replacement(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, JsonValue],
    module: ModuleType,
) -> None:
    executor, context, journal, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
    target = workspace / "target.txt"
    target.write_text("old generation", encoding="utf-8")
    old_target = workspace / "target.old"
    original_resolve = module.resolve_workspace_path

    def resolve_then_replace(*args: object, **kwargs: object) -> object:
        safe = original_resolve(*args, **kwargs)
        target.rename(old_target)
        target.write_text("new generation sentinel", encoding="utf-8")
        return safe

    monkeypatch.setattr(module, "resolve_workspace_path", resolve_then_replace)

    result = await executor.execute(
        ToolRequest(
            call_id=f"call_{tool_name}_generation_race",
            tool_name=tool_name,
            arguments=arguments,
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.CONFLICT
    assert target.read_text(encoding="utf-8") == "new generation sentinel"
    assert (await journal.seal(context.change_set_id or "")).files == []


@pytest.mark.asyncio
async def test_edit_file_rejects_in_place_change_during_before_snapshot(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, context, journal, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
    target = workspace / "target.txt"
    target.write_text("old content", encoding="utf-8")
    original_read = os.read
    original_fstat = os.fstat
    baseline_status: os.stat_result | None = None
    mutated = False
    observed_reads: list[bytes] = []

    def torn_read(descriptor: int, *, max_bytes: int | None) -> bytes:
        nonlocal baseline_status, mutated
        del max_bytes
        first = original_read(descriptor, 3)
        if not mutated:
            baseline_status = original_fstat(descriptor)
            with target.open("r+b") as writer:
                writer.seek(0)
                writer.write(b"new")
                writer.flush()
                os.fsync(writer.fileno())
            mutated = True
        data = first + original_read(descriptor, 64 * 1024)
        observed_reads.append(data)
        return data

    def stale_target_fstat(descriptor: int) -> os.stat_result:
        current = original_fstat(descriptor)
        if (
            mutated
            and baseline_status is not None
            and core_filesystem_module.identity(current)
            == core_filesystem_module.identity(baseline_status)
        ):
            return baseline_status
        return current

    monkeypatch.setattr(core_filesystem_module, "read_descriptor", torn_read)
    monkeypatch.setattr(os, "fstat", stale_target_fstat)
    monkeypatch.setattr(
        tools_filesystem_module,
        "_read_descriptor",
        torn_read,
        raising=False,
    )

    result = await executor.execute(
        ToolRequest(
            call_id="call_edit_read_race",
            tool_name="edit_file",
            arguments={
                "path": "target.txt",
                "old_string": "old",
                "new_string": "agent",
            },
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.CONFLICT
    assert target.read_text(encoding="utf-8") == "new content"
    assert observed_reads == [b"old content", b"new content"]
    assert (await journal.seal(context.change_set_id or "")).files == []


@pytest.mark.asyncio
async def test_write_file_approval_describes_the_real_target(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    executor, context, _, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
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
@pytest.mark.parametrize(
    "path",
    ["notes.txt:secret", ".env. ", "CON.txt", "dir/NUL.txt"],
)
async def test_windows_ambiguous_path_is_rejected_before_approval(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    executor, context, _, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
    approvals = 0

    async def approve(_request: ToolApprovalRequest) -> ToolApprovalDecision:
        nonlocal approvals
        approvals += 1
        return ToolApprovalDecision.ALLOW_ONCE

    monkeypatch.setattr(
        "awesome_agent.core.workspace.path_syntax.workspace_path_platform",
        lambda: "windows",
    )
    context = replace(
        context,
        permission_session=PermissionSession(),
        approval_resolver=approve,
    )

    result = await executor.execute(
        ToolRequest(
            call_id="call_windows_alias",
            tool_name="write_file",
            arguments={"path": path, "content": "content"},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert approvals == 0
    assert list(workspace.iterdir()) == []


@pytest.mark.asyncio
async def test_unsafe_write_is_not_described_or_approved(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, context, _, _, sink = await modifying_fixture(
        tmp_path,
        application_database,
    )
    resolution_calls = 0
    approvals = 0
    original_resolve = builtins_module.resolve_workspace_path

    def observed_resolve(*args: object, **kwargs: object) -> object:
        nonlocal resolution_calls
        resolution_calls += 1
        return original_resolve(*args, **kwargs)

    async def approve(_request: ToolApprovalRequest) -> ToolApprovalDecision:
        nonlocal approvals
        approvals += 1
        return ToolApprovalDecision.ALLOW_ONCE

    monkeypatch.setattr(builtins_module, "resolve_workspace_path", observed_resolve)
    context = replace(context, approval_resolver=approve)

    result = await executor.execute(
        ToolRequest(
            call_id="call_unsafe_write",
            tool_name="write_file",
            arguments={"path": "../outside.txt", "content": "private"},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.WORKSPACE_ESCAPE
    assert resolution_calls == 1
    assert approvals == 0
    assert all(event.payload.target is None for event in sink.events)  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_write_file_creates_and_overwrites_utf8_content(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    executor, context, journal, workspace, sink = await modifying_fixture(
        tmp_path, application_database
    )

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
    change_set = await journal.seal(context.change_set_id or "")
    assert len(change_set.files) == 2


@pytest.mark.asyncio
async def test_edit_file_replaces_one_exact_occurrence(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    executor, context, journal, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
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
    change_set = await journal.seal(context.change_set_id or "")
    assert len(change_set.files) == 1


@pytest.mark.asyncio
async def test_edit_file_rejects_ambiguous_replacement(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    executor, context, _, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
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
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "write_file",
            {"path": "parent/target.txt", "content": "after"},
        ),
        (
            "edit_file",
            {
                "path": "parent/target.txt",
                "old_string": "before",
                "new_string": "after",
            },
        ),
        ("delete", {"path": "parent/target.txt"}),
    ],
)
async def test_mutation_does_not_cross_a_parent_replaced_after_validation(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, JsonValue],
) -> None:
    executor, context, journal, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
    parent = workspace / "parent"
    parent.mkdir()
    (parent / "target.txt").write_text("before", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "target.txt"
    sentinel.write_text("outside", encoding="utf-8")
    replacement = _ParentReplacement(parent, outside)
    _race_first_journal_mutation(monkeypatch, journal, replacement)

    try:
        result = await executor.execute(
            ToolRequest(
                call_id=f"call_{tool_name}_parent_race",
                tool_name=tool_name,
                arguments=arguments,
            ),
            context=context,
        )

        assert sentinel.read_text(encoding="utf-8") == "outside"
        if replacement.replaced:
            assert result.status is ToolStatus.ERROR
            assert result.error is not None
            assert result.error.code is ToolErrorCode.CONFLICT
        else:
            # Windows pins each directory without FILE_SHARE_DELETE, so the
            # attempted parent rename is rejected before the bound mutation.
            assert os.name == "nt"
            assert result.status is ToolStatus.SUCCESS
    finally:
        replacement.restore()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("write_file", {"path": "linked.txt", "content": "after"}),
        (
            "edit_file",
            {
                "path": "linked.txt",
                "old_string": "outside",
                "new_string": "after",
            },
        ),
        ("delete", {"path": "linked.txt"}),
    ],
)
async def test_mutating_tools_reject_hard_linked_files(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    tool_name: str,
    arguments: dict[str, JsonValue],
) -> None:
    executor, context, _, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    os.link(outside, workspace / "linked.txt")

    result = await executor.execute(
        ToolRequest(
            call_id=f"call_{tool_name}_hardlink",
            tool_name=tool_name,
            arguments=arguments,
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED
    assert outside.read_text(encoding="utf-8") == "outside"
    assert (workspace / "linked.txt").exists()


@pytest.mark.asyncio
async def test_missing_change_set_is_invariant_failure_before_write(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    executor, context, _, workspace, _ = await modifying_fixture(
        tmp_path,
        application_database,
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
    application_database: ApplicationSQLite,
    target: str,
) -> None:
    executor, context, _, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
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
@pytest.mark.parametrize("target", [r"\outside.txt", "/outside.txt"])
async def test_delete_rejects_windows_rooted_paths_before_filesystem_access(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    executor, context, journal, _, _ = await modifying_fixture(
        tmp_path, application_database
    )
    monkeypatch.setattr(
        "awesome_agent.core.workspace.path_syntax.workspace_path_platform",
        lambda: "windows",
    )

    result = await executor.execute(
        ToolRequest(
            call_id="call_delete_rooted",
            tool_name="delete",
            arguments={"path": target},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.WORKSPACE_ESCAPE
    assert (await journal.seal(context.change_set_id or "")).files == []


@pytest.mark.asyncio
async def test_delete_capacity_fails_before_removing_any_node(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, context, _, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
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


@pytest.mark.asyncio
async def test_delete_rejects_regular_target_generation_replacement(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, context, journal, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
    target = workspace / "target.txt"
    target.write_text("old generation", encoding="utf-8")
    old_target = workspace / "target.old"
    original_inventory = tools_filesystem_module.WorkspaceDeleteTransaction.inventory
    replaced = False

    def inventory_after_replace(
        transaction: WorkspaceDeleteTransaction,
        *,
        validate_relative: Callable[[Path], None],
        max_nodes: int,
        max_bytes: int,
    ) -> list[SecureDeleteNode]:
        nonlocal replaced
        if not replaced:
            target.rename(old_target)
            target.write_text("new generation sentinel", encoding="utf-8")
            replaced = True
        return original_inventory(
            transaction,
            validate_relative=validate_relative,
            max_nodes=max_nodes,
            max_bytes=max_bytes,
        )

    monkeypatch.setattr(
        delete_module.WorkspaceDeleteTransaction,
        "inventory",
        inventory_after_replace,
    )

    result = await executor.execute(
        ToolRequest(
            call_id="call_delete_generation_race",
            tool_name="delete",
            arguments={"path": "target.txt"},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.CONFLICT
    assert target.read_text(encoding="utf-8") == "new generation sentinel"
    assert old_target.read_text(encoding="utf-8") == "old generation"
    assert (await journal.seal(context.change_set_id or "")).files == []


@pytest.mark.asyncio
async def test_delete_rejects_nested_directory_reparse_before_any_mutation(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    executor, context, journal, workspace, _ = await modifying_fixture(
        tmp_path, application_database
    )
    target = workspace / "target"
    target.mkdir()
    local = target / "local.txt"
    local.write_text("local", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("outside", encoding="utf-8")
    # Sort the unsafe node after a valid file to prove inventory is all-or-nothing.
    linked = target / "z-linked"
    if os.name == "nt":
        completed = subprocess.run(
            [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/c",
                "mklink",
                "/J",
                str(linked),
                str(outside),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
    else:
        linked.symlink_to(outside, target_is_directory=True)

    result = await executor.execute(
        ToolRequest(
            call_id="call_delete_reparse",
            tool_name="delete",
            arguments={"path": "target"},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED
    assert local.read_text(encoding="utf-8") == "local"
    assert sentinel.read_text(encoding="utf-8") == "outside"
    assert (await journal.seal(context.change_set_id or "")).files == []
