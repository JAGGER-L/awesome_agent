import json
from pathlib import Path

import pytest

from awesome_agent.core.events import CollectingEventSink, EventEmitter, EventType
from awesome_agent.core.tools import (
    ToolActivityDraft,
    ToolActivityWriter,
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolRequest,
    ToolStatus,
)
from awesome_agent.core.tools.executor import ToolExecutor
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.memory import LocalMemoryService, MemoryScope
from awesome_agent.memory.tools import refresh_local_memory_tools
from awesome_agent.paths import AwesomePaths

MEMORY_TOOLS = {
    "memory_add",
    "memory_list",
    "memory_remove",
    "memory_replace",
}


class ActivityWriter(ToolActivityWriter):
    def __init__(self) -> None:
        self.items: list[ToolActivityDraft] = []

    def finalize(self, activity: ToolActivityDraft) -> None:
        self.items.append(activity)


def _service(
    tmp_path: Path, workspace_key: str, *, enabled: bool
) -> LocalMemoryService:
    ids = iter(
        (
            "memory_11111111111111111111111111111111",
            "memory_22222222222222222222222222222222",
        )
    )
    return LocalMemoryService(
        paths=AwesomePaths.from_home(tmp_path / "home"),
        workspace_key=workspace_key,
        enabled=enabled,
        id_factory=lambda: next(ids),
    )


def _context(
    tmp_path: Path,
    *,
    origin: ToolExecutionOrigin = ToolExecutionOrigin.AGENT,
    active: bool = True,
    workspace_name: str = "workspace",
) -> tuple[ToolExecutionContext, CollectingEventSink, ActivityWriter]:
    workspace_path = tmp_path / workspace_name
    workspace_path.mkdir(exist_ok=True)
    workspace = resolve_workspace(workspace_path)
    sink = CollectingEventSink()
    writer = ActivityWriter()
    ticks = iter((1.0, 1.1))
    return (
        ToolExecutionContext(
            workspace=workspace,
            thread_id="thread",
            operation_id="operation",
            turn_id="turn" if origin is ToolExecutionOrigin.AGENT else None,
            origin=origin,
            emitter=EventEmitter(
                session_id="session",
                workspace_key=workspace.key,
                sink=sink,
            ),
            activity_writer=writer,
            monotonic=lambda: next(ticks),
            turn_active=active,
        ),
        sink,
        writer,
    )


def test_tool_exposure_tracks_local_switch_without_fixed_registry_size(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    service = _service(tmp_path, workspace.key, enabled=False)
    registry = ToolRegistry()

    refresh_local_memory_tools(registry, service)
    assert not MEMORY_TOOLS & {spec.name for spec in registry.specifications()}

    service.set_enabled(True)
    refresh_local_memory_tools(registry, service)
    specifications = {
        spec.name: spec
        for spec in registry.specifications()
        if spec.name in MEMORY_TOOLS
    }
    assert set(specifications) == MEMORY_TOOLS
    assert all(
        spec.display_metadata["category"] == "agent_core"
        for spec in specifications.values()
    )

    service.set_enabled(False)
    refresh_local_memory_tools(registry, service)
    assert not MEMORY_TOOLS & {spec.name for spec in registry.specifications()}


@pytest.mark.asyncio
async def test_visible_add_and_list_flow_through_executor(tmp_path: Path) -> None:
    context, sink, writer = _context(tmp_path)
    service = _service(tmp_path, context.workspace.key, enabled=True)
    registry = ToolRegistry()
    refresh_local_memory_tools(registry, service)
    executor = ToolExecutor(registry)
    observed = service.snapshot(MemoryScope.USER)

    added = await executor.execute(
        ToolRequest(
            call_id="add",
            tool_name="memory_add",
            arguments={
                "scope": "user",
                "content": "Prefer concise answers.",
                "expected_hash": observed.content_hash,
            },
        ),
        context=context,
    )
    assert added.status is ToolStatus.SUCCESS
    payload = json.loads(added.content)
    assert payload["entry_id"].startswith("memory_")
    assert "markdown" not in added.content
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
    ]
    assert writer.items[0].tool_name == "memory_add"

    list_context, _, _ = _context(tmp_path, workspace_name="workspace")
    listed = await executor.execute(
        ToolRequest(
            call_id="list",
            tool_name="memory_list",
            arguments={"scope": "user"},
        ),
        context=list_context,
    )
    assert listed.status is ToolStatus.SUCCESS
    assert json.loads(listed.content)["entries"][0]["content"] == (
        "Prefer concise answers."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("origin", "active", "workspace_name", "error_code"),
    [
        (
            ToolExecutionOrigin.DIRECT,
            True,
            "workspace",
            ToolErrorCode.PERMISSION_DENIED,
        ),
        (
            ToolExecutionOrigin.AGENT,
            False,
            "workspace",
            ToolErrorCode.PERMISSION_DENIED,
        ),
        (ToolExecutionOrigin.AGENT, True, "other", ToolErrorCode.PERMISSION_DENIED),
    ],
)
async def test_mutations_require_active_agent_turn_in_matching_workspace(
    tmp_path: Path,
    origin: ToolExecutionOrigin,
    active: bool,
    workspace_name: str,
    error_code: ToolErrorCode,
) -> None:
    canonical_path = tmp_path / "workspace"
    canonical_path.mkdir()
    canonical = resolve_workspace(canonical_path)
    service = _service(tmp_path, canonical.key, enabled=True)
    registry = ToolRegistry()
    refresh_local_memory_tools(registry, service)
    context, _, _ = _context(
        tmp_path,
        origin=origin,
        active=active,
        workspace_name=workspace_name,
    )

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="guarded",
            tool_name="memory_add",
            arguments={
                "scope": "user",
                "content": "Prefer concise answers.",
                "expected_hash": service.snapshot(MemoryScope.USER).content_hash,
            },
        ),
        context=context,
    )

    assert result.error is not None
    assert result.error.code is error_code
    assert service.list(MemoryScope.USER) == ()


@pytest.mark.asyncio
async def test_missing_and_stale_hashes_are_normalized(tmp_path: Path) -> None:
    context, _, _ = _context(tmp_path)
    service = _service(tmp_path, context.workspace.key, enabled=True)
    registry = ToolRegistry()
    refresh_local_memory_tools(registry, service)
    executor = ToolExecutor(registry)

    missing = await executor.execute(
        ToolRequest(
            call_id="missing",
            tool_name="memory_add",
            arguments={"scope": "user", "content": "fact"},
        ),
        context=context,
    )
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.INVALID_ARGUMENTS

    stale_context, _, _ = _context(tmp_path)
    stale = await executor.execute(
        ToolRequest(
            call_id="stale",
            tool_name="memory_add",
            arguments={
                "scope": "user",
                "content": "fact",
                "expected_hash": "0" * 64,
            },
        ),
        context=stale_context,
    )
    assert stale.error is not None
    assert stale.error.code is ToolErrorCode.MEMORY_CONFLICT
