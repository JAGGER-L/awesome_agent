from pathlib import Path
from time import monotonic
from unittest.mock import Mock

import pytest
from pydantic import JsonValue

from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools import (
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolExecutor,
    ToolRequest,
    ToolResult,
    ToolStatus,
)
from awesome_agent.core.tools.builtins import register_read_tools
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace


def read_executor(
    workspace: Path,
) -> tuple[ToolExecutor, ToolExecutionContext]:
    registry = ToolRegistry()
    register_read_tools(registry)
    identity = resolve_workspace(workspace)
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
        activity_writer=Mock(),
        monotonic=monotonic,
    )
    return ToolExecutor(registry), context


async def execute(
    executor: ToolExecutor,
    context: ToolExecutionContext,
    tool_name: str,
    arguments: dict[str, JsonValue],
) -> ToolResult:
    return await executor.execute(
        ToolRequest(
            call_id=f"call_{tool_name}",
            tool_name=tool_name,
            arguments=arguments,
        ),
        context=context,
    )


@pytest.mark.asyncio
async def test_ls_is_sorted_bounded_and_hides_sensitive_entries(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    (workspace / "folder").mkdir()
    (workspace / "long.txt").write_text("long", encoding="utf-8")
    (workspace / ".env").write_text("SECRET=value", encoding="utf-8")
    (workspace / ".git").mkdir()
    executor, context = read_executor(workspace)

    result = await execute(executor, context, "ls", {"path": "."})

    assert result.status is ToolStatus.SUCCESS
    entries = result.metadata["entries"]
    assert isinstance(entries, list)
    assert [item["name"] for item in entries if isinstance(item, dict)] == [
        "a.txt",
        "folder",
        "long.txt",
    ]
    assert result.metadata["truncated"] is False


@pytest.mark.asyncio
async def test_ls_honors_max_entries(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (workspace / name).write_text(name, encoding="utf-8")
    executor, context = read_executor(workspace)

    result = await execute(
        executor,
        context,
        "ls",
        {"path": ".", "max_entries": 2},
    )

    entries = result.metadata["entries"]
    assert isinstance(entries, list)
    assert len(entries) == 2
    assert result.metadata["truncated"] is True


@pytest.mark.asyncio
async def test_read_file_returns_at_most_500_numbered_lines(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = "\n".join(f"line {number}" for number in range(1, 601))
    (workspace / "long.txt").write_text(content, encoding="utf-8")
    executor, context = read_executor(workspace)

    result = await execute(
        executor,
        context,
        "read_file",
        {"path": "long.txt"},
    )

    assert result.status is ToolStatus.SUCCESS
    assert result.metadata["start_line"] == 1
    assert result.metadata["end_line"] == 500
    assert result.metadata["total_lines"] == 600
    assert result.metadata["truncated"] is True
    assert result.content.startswith("1: line 1\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["folder/binary.bin", ".env"])
async def test_read_file_rejects_binary_and_sensitive_files(
    tmp_path: Path,
    path: str,
) -> None:
    workspace = tmp_path / "workspace"
    folder = workspace / "folder"
    folder.mkdir(parents=True)
    (folder / "binary.bin").write_bytes(b"before\x00after")
    (workspace / ".env").write_text("SECRET=value", encoding="utf-8")
    executor, context = read_executor(workspace)

    result = await execute(executor, context, "read_file", {"path": path})

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code in {
        ToolErrorCode.EXECUTION_FAILED,
        ToolErrorCode.PERMISSION_DENIED,
    }


def create_search_workspace(workspace: Path) -> bool:
    source = workspace / "src"
    source.mkdir(parents=True)
    (source / "a.py").write_text("Needle one\nother", encoding="utf-8")
    (source / "b.txt").write_text("Needle two", encoding="utf-8")
    (source / "c.py").write_text("Needle three", encoding="utf-8")
    (source / "credentials.py").write_text("Needle secret", encoding="utf-8")
    (source / "binary.bin").write_bytes(b"Needle\x00binary")
    hidden = workspace / ".git"
    hidden.mkdir()
    (hidden / "hidden.py").write_text("Needle hidden", encoding="utf-8")
    outside = workspace.parent / "outside"
    outside.mkdir()
    (outside / "leak.py").write_text("Needle leak", encoding="utf-8")
    try:
        (source / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        return False
    return True


@pytest.mark.asyncio
async def test_glob_returns_only_sorted_safe_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    create_search_workspace(workspace)
    executor, context = read_executor(workspace)

    result = await execute(
        executor,
        context,
        "glob",
        {"pattern": "**/*", "path": "."},
    )

    assert result.status is ToolStatus.SUCCESS
    matches = result.metadata["matches"]
    assert isinstance(matches, list)
    assert matches == ["src/a.py", "src/b.txt", "src/c.py"]
    assert result.metadata["truncated"] is False


@pytest.mark.asyncio
async def test_grep_is_deterministic_bounded_and_filters_unsafe_content(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    create_search_workspace(workspace)
    executor, context = read_executor(workspace)

    result = await execute(
        executor,
        context,
        "grep",
        {
            "pattern": "Needle",
            "path": ".",
            "include": "*.py",
            "max_results": 1,
        },
    )

    assert result.status is ToolStatus.SUCCESS
    matches = result.metadata["matches"]
    assert isinstance(matches, list)
    assert matches == [{"path": "src/a.py", "line": 1, "text": "Needle one"}]
    assert result.metadata["truncated"] is True


@pytest.mark.asyncio
async def test_grep_invalid_regex_is_invalid_arguments(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("text", encoding="utf-8")
    executor, context = read_executor(workspace)

    result = await execute(
        executor,
        context,
        "grep",
        {"pattern": "(", "path": ".", "regex": True},
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS


@pytest.mark.asyncio
async def test_search_does_not_follow_directory_symlinks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.py").write_text("Needle leak", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are not available on this platform.")
    executor, context = read_executor(workspace)

    glob_result = await execute(
        executor,
        context,
        "glob",
        {"pattern": "**/*", "path": "."},
    )
    grep_result = await execute(
        executor,
        context,
        "grep",
        {"pattern": "Needle", "path": "."},
    )

    assert glob_result.metadata["matches"] == []
    assert grep_result.metadata["matches"] == []
