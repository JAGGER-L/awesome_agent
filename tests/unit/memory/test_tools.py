import asyncio
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel

import awesome_agent.core.tools.executor as tool_executor
import awesome_agent.memory.tools as memory_tools
from awesome_agent.core.cancellation import run_cancellation_safe_blocking_call
from awesome_agent.core.events import CollectingEventSink, EventEmitter, EventType
from awesome_agent.core.resource_lock import (
    ResourceLockTimeout,
    ResourceLockUnavailable,
    exclusive_resource_lock,
)
from awesome_agent.core.tools import (
    ToolActivityDraft,
    ToolActivityWriter,
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolOutput,
    ToolRequest,
    ToolSpec,
    ToolStatus,
)
from awesome_agent.core.tools.executor import ToolExecutor
from awesome_agent.core.tools.registry import (
    MAX_REGISTERED_TOOLS,
    ToolRegistry,
    ToolRegistryLimitError,
)
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.memory import LocalMemoryService, MemoryScope
from awesome_agent.memory.tools import MemoryListArguments, refresh_local_memory_tools
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

    async def finalize(self, activity: ToolActivityDraft) -> None:
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


def _hold_resource_lock(path: Path, entered: threading.Event, seconds: float) -> None:
    with exclusive_resource_lock(path):
        entered.set()
        time.sleep(seconds)


def _fill_registry(registry: ToolRegistry, count: int) -> None:
    async def placeholder_handler(
        _arguments: BaseModel,
        _context: ToolExecutionContext,
    ) -> ToolOutput:
        return ToolOutput(content="unused")

    for index in range(count):
        registry.register(
            spec=ToolSpec(
                name=f"placeholder_{index}",
                description="Reserved test tool.",
                input_schema={},
                capability="workspace.read",
                read_only=True,
            ),
            input_model=MemoryListArguments,
            handler=placeholder_handler,
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


@pytest.mark.parametrize("base_count", [125, 126, 127, 128])
def test_memory_tool_refresh_is_atomic_when_registry_has_no_four_tool_capacity(
    tmp_path: Path,
    base_count: int,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    service = _service(tmp_path, workspace.key, enabled=True)
    registry = ToolRegistry()
    _fill_registry(registry, base_count)
    before = registry.specifications()

    with pytest.raises(ToolRegistryLimitError, match="128-tool aggregate limit"):
        refresh_local_memory_tools(registry, service)

    assert registry.specifications() == before
    assert not MEMORY_TOOLS & {spec.name for spec in registry.specifications()}


def test_memory_tool_refresh_fills_exact_128_tool_budget(tmp_path: Path) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    service = _service(tmp_path, workspace.key, enabled=True)
    registry = ToolRegistry()
    _fill_registry(registry, MAX_REGISTERED_TOOLS - len(MEMORY_TOOLS))

    refresh_local_memory_tools(registry, service)

    assert len(registry.specifications()) == MAX_REGISTERED_TOOLS
    assert {spec.name for spec in registry.specifications()} >= MEMORY_TOOLS


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
    ("failure", "error_code", "retryable"),
    [
        (ResourceLockTimeout(), ToolErrorCode.TIMEOUT, True),
        (ResourceLockUnavailable(), ToolErrorCode.STATE_UNAVAILABLE, False),
    ],
)
async def test_memory_tool_normalizes_resource_lock_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    error_code: ToolErrorCode,
    retryable: bool,
) -> None:
    context, sink, writer = _context(tmp_path)
    service = _service(tmp_path, context.workspace.key, enabled=True)

    def fail(*_: object, **__: object) -> object:
        raise failure

    monkeypatch.setattr(service, "add", fail)
    registry = ToolRegistry()
    refresh_local_memory_tools(registry, service)
    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="locked-memory",
            tool_name="memory_add",
            arguments={
                "scope": "user",
                "content": "Remember safely.",
                "expected_hash": "0" * 64,
            },
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is error_code
    assert result.error.retryable is retryable
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_FAILED,
    ]
    assert len(writer.items) == 1
    assert writer.items[0].error_code == error_code.value


@pytest.mark.asyncio
async def test_memory_tool_lock_wait_keeps_event_loop_schedulable(
    tmp_path: Path,
) -> None:
    context, _, _ = _context(tmp_path)
    service = _service(tmp_path, context.workspace.key, enabled=True)
    registry = ToolRegistry()
    refresh_local_memory_tools(registry, service)
    registered = registry.resolve("memory_add")
    assert registered is not None
    observed = service.snapshot(MemoryScope.USER)
    arguments = registered.input_model.model_validate(
        {
            "scope": "user",
            "content": "Remember without blocking the event loop.",
            "expected_hash": observed.content_hash,
        }
    )
    memory_path = AwesomePaths.from_home(tmp_path / "home").user_memory_file
    entered = threading.Event()
    holder = threading.Thread(
        target=_hold_resource_lock,
        args=(memory_path, entered, 0.4),
        daemon=True,
    )
    holder.start()
    assert await asyncio.to_thread(entered.wait, 1.0)

    async def invoke() -> ToolOutput:
        return await registered.handler(arguments, context)

    operation = asyncio.create_task(invoke())
    heartbeat = asyncio.create_task(asyncio.sleep(0.05))
    try:
        await asyncio.wait_for(heartbeat, timeout=0.2)
        assert not operation.done()
        output = await operation
    finally:
        await asyncio.to_thread(holder.join, 1.0)

    assert json.loads(output.content)["status"] == "added"


@pytest.mark.asyncio
async def test_cancelled_memory_mutation_keeps_foreground_until_worker_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, sink, writer = _context(tmp_path)
    service = _service(tmp_path, context.workspace.key, enabled=True)
    observed = service.snapshot(MemoryScope.USER)
    entered = threading.Event()
    release = threading.Event()
    original_add = service.add
    original_blocking_call = run_cancellation_safe_blocking_call

    def delayed_add(
        scope: MemoryScope,
        content: str,
        *,
        expected_hash: str,
    ) -> object:
        entered.set()
        if not release.wait(1.0):
            raise RuntimeError("memory worker release was not scheduled")
        return original_add(scope, content, expected_hash=expected_hash)

    async def short_bounded_call(call: Callable[[], object]) -> object:
        return await original_blocking_call(
            call,
            cleanup_timeout_seconds=1.0,
        )

    monkeypatch.setattr(service, "add", delayed_add)
    monkeypatch.setattr(
        memory_tools,
        "run_cancellation_safe_blocking_call",
        short_bounded_call,
    )
    monkeypatch.setattr(
        memory_tools,
        "_MEMORY_HANDLER_CANCELLATION_GRACE_SECONDS",
        1.5,
        raising=False,
    )
    monkeypatch.setattr(
        tool_executor,
        "_HANDLER_CANCELLATION_GRACE_SECONDS",
        0.05,
    )
    registry = ToolRegistry()
    refresh_local_memory_tools(registry, service)
    registered = registry.resolve("memory_add")
    assert registered is not None
    assert registered.cancellation_grace_seconds == 1.5
    task = asyncio.create_task(
        ToolExecutor(registry).execute(
            ToolRequest(
                call_id="cancelled-memory",
                tool_name="memory_add",
                arguments={
                    "scope": "user",
                    "content": "Persist before cancellation is terminal.",
                    "expected_hash": observed.content_hash,
                },
            ),
            context=context,
        )
    )
    assert await asyncio.to_thread(entered.wait, 1.0)

    task.cancel("cancel memory mutation")
    try:
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError, match="cancel memory mutation"):
        await asyncio.wait_for(task, timeout=2.0)

    assert service.list(MemoryScope.USER)[0].content == (
        "Persist before cancellation is terminal."
    )
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_CANCELLED,
    ]
    assert len(writer.items) == 1
    assert writer.items[0].outcome == "cancelled"


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
