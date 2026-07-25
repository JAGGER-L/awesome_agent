import importlib
import os
import subprocess
from pathlib import Path
from time import monotonic
from types import ModuleType
from unittest.mock import Mock

import pytest
from pydantic import JsonValue

import awesome_agent.core.filesystem as core_filesystem_module
import awesome_agent.core.tools.filesystem as tools_filesystem_module
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

read_file_module = importlib.import_module(
    "awesome_agent.core.tools.builtins.read_file"
)
listing_module = importlib.import_module("awesome_agent.core.tools.builtins.listing")
search_module = importlib.import_module("awesome_agent.core.tools.builtins.search")
file_enumerator_module = importlib.import_module(
    "awesome_agent.core.tools.builtins.file_enumerator"
)


def _directory_link(target: Path, link: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        return
    os.symlink(target, link, target_is_directory=True)


def _remove_directory_link(link: Path) -> None:
    if os.name == "nt":
        link.rmdir()
    else:
        link.unlink()


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
async def test_ls_rejects_directory_replaced_by_link_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "target"
    target.mkdir(parents=True)
    (target / "inside.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside-ls"
    outside.mkdir()
    (outside / "EXTERNAL-LS-SENTINEL.txt").write_text(
        "outside",
        encoding="utf-8",
    )
    original_target = workspace / "target.original"
    original_resolve = listing_module.resolve_workspace_path
    replaced = False

    def resolve_then_replace(*args: object, **kwargs: object) -> object:
        nonlocal replaced
        safe = original_resolve(*args, **kwargs)
        target.rename(original_target)
        _directory_link(outside, target)
        replaced = True
        return safe

    monkeypatch.setattr(
        listing_module,
        "resolve_workspace_path",
        resolve_then_replace,
    )
    executor, context = read_executor(workspace)

    try:
        result = await execute(executor, context, "ls", {"path": "target"})

        assert result.status is ToolStatus.ERROR
        assert "EXTERNAL-LS-SENTINEL" not in result.content
    finally:
        if replaced:
            _remove_directory_link(target)
            original_target.rename(target)


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
async def test_read_file_rejects_replacement_of_bound_workspace_root(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "target.txt").write_text("original", encoding="utf-8")
    executor, context = read_executor(workspace)
    original = tmp_path / "workspace-original"
    workspace.rename(original)
    workspace.mkdir()
    (workspace / "target.txt").write_text("replacement sentinel", encoding="utf-8")

    result = await execute(
        executor,
        context,
        "read_file",
        {"path": "target.txt"},
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.CONFLICT
    assert "replacement sentinel" not in result.content


@pytest.mark.asyncio
async def test_read_file_rechecks_workspace_root_when_opening_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "target.txt").write_text("original", encoding="utf-8")
    executor, context = read_executor(workspace)
    original = tmp_path / "workspace-original"
    original_resolve = read_file_module.resolve_workspace_path

    def resolve_then_replace(*args: object, **kwargs: object) -> object:
        safe = original_resolve(*args, **kwargs)
        workspace.rename(original)
        workspace.mkdir()
        (workspace / "target.txt").write_text(
            "replacement sentinel",
            encoding="utf-8",
        )
        return safe

    monkeypatch.setattr(
        read_file_module,
        "resolve_workspace_path",
        resolve_then_replace,
    )

    result = await execute(
        executor,
        context,
        "read_file",
        {"path": "target.txt"},
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.CONFLICT
    assert "replacement sentinel" not in result.content


@pytest.mark.asyncio
async def test_read_file_rejects_regular_file_generation_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("old generation", encoding="utf-8")
    old_target = workspace / "target.old"
    original_resolve = read_file_module.resolve_workspace_path

    def resolve_then_replace(*args: object, **kwargs: object) -> object:
        safe = original_resolve(*args, **kwargs)
        target.rename(old_target)
        target.write_text("new generation sentinel", encoding="utf-8")
        return safe

    monkeypatch.setattr(
        read_file_module,
        "resolve_workspace_path",
        resolve_then_replace,
    )
    executor, context = read_executor(workspace)

    result = await execute(
        executor,
        context,
        "read_file",
        {"path": "target.txt"},
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.CONFLICT
    assert "new generation sentinel" not in result.content


@pytest.mark.asyncio
async def test_read_file_rejects_in_place_change_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("old content", encoding="utf-8")
    original_read = os.read

    def torn_read(descriptor: int, *, max_bytes: int | None) -> bytes:
        del max_bytes
        first = original_read(descriptor, 3)
        with target.open("r+b") as writer:
            writer.seek(0)
            writer.write(b"new")
            writer.flush()
            os.fsync(writer.fileno())
        return first + original_read(descriptor, 64 * 1024)

    monkeypatch.setattr(core_filesystem_module, "read_descriptor", torn_read)
    monkeypatch.setattr(
        tools_filesystem_module,
        "_read_descriptor",
        torn_read,
        raising=False,
    )
    executor, context = read_executor(workspace)

    result = await execute(
        executor,
        context,
        "read_file",
        {"path": "target.txt"},
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.CONFLICT
    assert target.read_text(encoding="utf-8") == "new content"


@pytest.mark.asyncio
async def test_read_file_does_not_follow_a_parent_replaced_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    parent = workspace / "parent"
    parent.mkdir(parents=True)
    (parent / "target.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "target.txt"
    sentinel.write_text("outside", encoding="utf-8")
    original_parent = workspace / "parent.original"
    original_resolve = read_file_module.resolve_workspace_path
    replaced = False

    def resolve_then_replace(*args: object, **kwargs: object) -> object:
        nonlocal replaced
        safe = original_resolve(*args, **kwargs)
        parent.rename(original_parent)
        if os.name == "nt":
            completed = subprocess.run(
                [
                    os.environ.get("COMSPEC", "cmd.exe"),
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(parent),
                    str(outside),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            assert completed.returncode == 0, completed.stderr
        else:
            parent.symlink_to(outside, target_is_directory=True)
        replaced = True
        return safe

    monkeypatch.setattr(
        read_file_module,
        "resolve_workspace_path",
        resolve_then_replace,
    )
    executor, context = read_executor(workspace)

    try:
        result = await execute(
            executor,
            context,
            "read_file",
            {"path": "parent/target.txt"},
        )

        assert result.status is ToolStatus.ERROR
        assert "outside" not in result.content
        assert sentinel.read_text(encoding="utf-8") == "outside"
    finally:
        if replaced:
            if os.name == "nt":
                parent.rmdir()
            else:
                parent.unlink()
            original_parent.rename(parent)


@pytest.mark.asyncio
async def test_read_file_rejects_hard_linked_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    os.link(outside, workspace / "linked.txt")
    executor, context = read_executor(workspace)

    result = await execute(
        executor,
        context,
        "read_file",
        {"path": "linked.txt"},
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED
    assert "outside" not in result.content


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
    assert matches == ["src/a.py", "src/b.txt", "src/binary.bin", "src/c.py"]
    assert result.metadata["truncated"] is False


@pytest.mark.asyncio
async def test_glob_matches_metadata_without_reading_file_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_bytes(b"\x00binary")
    executor, context = read_executor(workspace)

    def fail_read(_path: Path) -> bytes:
        raise AssertionError("Glob must not read file contents.")

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    result = await execute(executor, context, "glob", {"pattern": "*.py"})

    assert result.status is ToolStatus.SUCCESS
    assert result.metadata["matches"] == ["a.py"]


@pytest.mark.asyncio
async def test_grep_applies_include_before_reading_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    included = workspace / "included.py"
    excluded = workspace / "excluded.txt"
    included.write_text("Needle", encoding="utf-8")
    excluded.write_text("Needle", encoding="utf-8")
    original = Path.read_bytes

    def guarded_read(path: Path) -> bytes:
        if path == excluded:
            raise AssertionError("Excluded files must not be read.")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    executor, context = read_executor(workspace)
    result = await execute(
        executor,
        context,
        "grep",
        {"pattern": "Needle", "include": "*.py"},
    )

    assert result.status is ToolStatus.SUCCESS
    assert result.metadata["matches"] == [
        {"path": "included.py", "line": 1, "text": "Needle"}
    ]


@pytest.mark.asyncio
async def test_grep_rejects_file_replaced_by_hardlink_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.py"
    target.write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside-grep.txt"
    outside.write_text("EXTERNAL-GREP-HARDLINK-SENTINEL", encoding="utf-8")
    original_resolve = file_enumerator_module.resolve_workspace_path
    replaced = False

    def resolve_then_replace(*args: object, **kwargs: object) -> object:
        nonlocal replaced
        safe = original_resolve(*args, **kwargs)
        requested = args[1] if len(args) > 1 else kwargs.get("requested")
        if requested == "target.py" and not replaced:
            target.unlink()
            os.link(outside, target)
            replaced = True
        return safe

    monkeypatch.setattr(
        file_enumerator_module,
        "resolve_workspace_path",
        resolve_then_replace,
    )
    executor, context = read_executor(workspace)

    result = await execute(
        executor,
        context,
        "grep",
        {"pattern": "EXTERNAL-GREP-HARDLINK-SENTINEL"},
    )

    assert result.status is ToolStatus.ERROR
    assert "EXTERNAL-GREP-HARDLINK-SENTINEL" not in result.content


@pytest.mark.asyncio
async def test_directory_read_tools_skip_preexisting_hardlinks(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "inside.py").write_text("Needle inside", encoding="utf-8")
    outside = tmp_path / "outside-hardlink.txt"
    outside.write_text("EXTERNAL-STATIC-HARDLINK-SENTINEL", encoding="utf-8")
    os.link(outside, workspace / "linked.py")
    executor, context = read_executor(workspace)

    ls_result = await execute(executor, context, "ls", {"path": "."})
    glob_result = await execute(executor, context, "glob", {"pattern": "*.py"})
    grep_result = await execute(
        executor,
        context,
        "grep",
        {"pattern": "EXTERNAL-STATIC-HARDLINK-SENTINEL"},
    )

    assert ls_result.status is ToolStatus.SUCCESS
    assert glob_result.status is ToolStatus.SUCCESS
    assert grep_result.status is ToolStatus.SUCCESS
    assert "linked.py" not in ls_result.content
    assert glob_result.metadata["matches"] == ["inside.py"]
    assert grep_result.metadata["matches"] == []
    assert "EXTERNAL-STATIC-HARDLINK-SENTINEL" not in grep_result.content


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "sentinel"),
    [
        ("glob", {"pattern": "*", "path": "target"}, "EXTERNAL-GLOB-SENTINEL"),
        (
            "grep",
            {"pattern": "EXTERNAL-GREP-SENTINEL", "path": "target"},
            "EXTERNAL-GREP-SENTINEL",
        ),
    ],
)
async def test_search_rejects_root_replaced_by_link_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, JsonValue],
    sentinel: str,
) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "target"
    target.mkdir(parents=True)
    (target / "inside.py").write_text("inside", encoding="utf-8")
    outside = tmp_path / f"outside-{tool_name}"
    outside.mkdir()
    outside_name = "EXTERNAL-GLOB-SENTINEL.py" if tool_name == "glob" else "outside.py"
    (outside / outside_name).write_text(
        "EXTERNAL-GREP-SENTINEL",
        encoding="utf-8",
    )
    original_target = workspace / "target.original"
    original_resolve = search_module.resolve_workspace_path
    replaced = False

    def resolve_then_replace(*args: object, **kwargs: object) -> object:
        nonlocal replaced
        safe = original_resolve(*args, **kwargs)
        if not replaced:
            target.rename(original_target)
            _directory_link(outside, target)
            replaced = True
        return safe

    monkeypatch.setattr(
        search_module,
        "resolve_workspace_path",
        resolve_then_replace,
    )
    executor, context = read_executor(workspace)

    try:
        result = await execute(executor, context, tool_name, arguments)

        assert result.status is ToolStatus.ERROR
        assert sentinel not in result.content
    finally:
        if replaced:
            _remove_directory_link(target)
            original_target.rename(target)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "module"),
    [
        ("ls", {"path": "target"}, listing_module),
        ("glob", {"pattern": "*.py", "path": "target"}, search_module),
        (
            "grep",
            {"pattern": "new generation sentinel", "path": "target"},
            search_module,
        ),
    ],
)
async def test_directory_read_tools_reject_regular_root_generation_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, JsonValue],
    module: ModuleType,
) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "target"
    target.mkdir(parents=True)
    (target / "old.py").write_text("old generation", encoding="utf-8")
    old_target = workspace / "target.old"
    original_resolve = module.resolve_workspace_path
    replaced = False

    def resolve_then_replace(*args: object, **kwargs: object) -> object:
        nonlocal replaced
        safe = original_resolve(*args, **kwargs)
        requested = args[1] if len(args) > 1 else kwargs.get("requested")
        if requested == "target" and not replaced:
            target.rename(old_target)
            target.mkdir()
            (target / "new.py").write_text(
                "new generation sentinel",
                encoding="utf-8",
            )
            replaced = True
        return safe

    monkeypatch.setattr(module, "resolve_workspace_path", resolve_then_replace)
    executor, context = read_executor(workspace)

    result = await execute(executor, context, tool_name, arguments)

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.CONFLICT
    assert "new generation sentinel" not in result.content
