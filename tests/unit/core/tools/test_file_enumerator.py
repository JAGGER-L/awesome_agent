from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from unittest.mock import Mock

import pytest

from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools import (
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolExecutor,
    ToolRequest,
    ToolStatus,
)
from awesome_agent.core.tools.builtins import register_read_tools
from awesome_agent.core.tools.builtins.file_enumerator import (
    EnumeratedFile,
    ScanCancellation,
    ScanCancelled,
    enumerate_workspace_files,
)
from awesome_agent.core.tools.builtins.search import GlobArguments, glob_files
from awesome_agent.core.tools.policy import SafeWorkspacePath, resolve_workspace_path
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace


def _context(workspace: Path) -> ToolExecutionContext:
    identity = resolve_workspace(workspace)
    return ToolExecutionContext(
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


def test_enumerator_prunes_defaults_but_allows_explicit_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("main", encoding="utf-8")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "package.js").write_text("package", encoding="utf-8")
    (workspace / ".venv").mkdir()
    (workspace / ".venv" / "python.exe").write_bytes(b"binary")
    context = _context(workspace)
    root = resolve_workspace_path(
        context.workspace, ".", must_exist=True, expected_kind="directory"
    )

    files = list(
        enumerate_workspace_files(
            root,
            context,
            cancellation=ScanCancellation(),
            prune_defaults=True,
        )
    )

    assert [item.relative for item in files] == ["src/main.py"]
    explicit = resolve_workspace_path(
        context.workspace,
        "node_modules",
        must_exist=True,
        expected_kind="directory",
    )
    explicit_files = list(
        enumerate_workspace_files(
            explicit,
            context,
            cancellation=ScanCancellation(),
            prune_defaults=True,
        )
    )
    assert [item.relative for item in explicit_files] == ["node_modules/package.js"]


def test_scan_cancellation_is_cooperative(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.py").write_text("content", encoding="utf-8")
    context = _context(workspace)
    root = resolve_workspace_path(
        context.workspace, ".", must_exist=True, expected_kind="directory"
    )
    cancellation = ScanCancellation()
    cancellation.cancel()

    with pytest.raises(ScanCancelled):
        list(
            enumerate_workspace_files(
                root,
                context,
                cancellation=cancellation,
                prune_defaults=True,
            )
        )


@pytest.mark.asyncio
async def test_glob_worker_keeps_loop_responsive_and_stops_on_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    started = Event()
    stopped = Event()

    def gated_files(
        root: SafeWorkspacePath,
        current: ToolExecutionContext,
        *,
        cancellation: ScanCancellation,
        prune_defaults: bool,
    ) -> Iterator[EnumeratedFile]:
        del root, current, prune_defaults
        started.set()
        try:
            while True:
                cancellation.raise_if_cancelled()
                sleep(0.001)
        finally:
            stopped.set()
        yield

    monkeypatch.setattr(
        "awesome_agent.core.tools.builtins.search.enumerate_workspace_files",
        gated_files,
    )
    task = asyncio.create_task(glob_files(GlobArguments(pattern="*"), context))
    assert await asyncio.to_thread(started.wait, 1.0)
    loop_progressed = False

    def mark_progress() -> None:
        nonlocal loop_progressed
        loop_progressed = True

    asyncio.get_running_loop().call_soon(mark_progress)
    await asyncio.sleep(0)
    assert loop_progressed is True
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_glob_uses_fixed_prefix_and_stops_after_truncation_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    context = _context(workspace)
    visited: list[str] = []
    yielded: list[str] = []

    def files(
        root: SafeWorkspacePath,
        current: ToolExecutionContext,
        *,
        cancellation: ScanCancellation,
        prune_defaults: bool,
    ) -> Iterator[EnumeratedFile]:
        del current, prune_defaults
        visited.append(root.relative.as_posix())
        for name in ("a.py", "b.py", "c.py"):
            cancellation.raise_if_cancelled()
            yielded.append(name)
            yield EnumeratedFile(
                relative=f"src/{name}",
                resolved=workspace / "src" / name,
            )

    monkeypatch.setattr(
        "awesome_agent.core.tools.builtins.search.enumerate_workspace_files",
        files,
    )
    result = await glob_files(
        GlobArguments(pattern="src/*.py", max_results=1),
        context,
    )

    assert visited == ["src"]
    assert yielded == ["a.py", "b.py"]
    assert result.metadata == {"matches": ["src/a.py"], "truncated": True}


@pytest.mark.asyncio
async def test_executor_timeout_waits_for_scan_worker_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    stopped = Event()

    def endless_files(
        root: SafeWorkspacePath,
        current: ToolExecutionContext,
        *,
        cancellation: ScanCancellation,
        prune_defaults: bool,
    ) -> Iterator[EnumeratedFile]:
        del root, current, prune_defaults
        try:
            while True:
                cancellation.raise_if_cancelled()
                sleep(0.001)
        finally:
            stopped.set()
        yield

    monkeypatch.setattr(
        "awesome_agent.core.tools.builtins.search.enumerate_workspace_files",
        endless_files,
    )
    registry = ToolRegistry()
    register_read_tools(registry)
    executor = ToolExecutor(registry, timeout_seconds=0.01)

    result = await executor.execute(
        ToolRequest(call_id="call_1", tool_name="glob", arguments={"pattern": "*"}),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code.value == "timeout"
    assert stopped.is_set()
