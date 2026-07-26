import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from awesome_agent.core.events import (
    CollectingEventSink,
    EventEmitter,
    EventEnvelope,
    EventType,
)
from awesome_agent.core.tools import (
    ExpectedToolFailure,
    ToolActivityDraft,
    ToolActivityWriter,
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolHandler,
    ToolOutput,
    ToolRequest,
    ToolSpec,
    ToolStatus,
)
from awesome_agent.core.tools.errors import ToolInvariantError
from awesome_agent.core.tools.executor import ToolExecutor
from awesome_agent.core.tools.permissions import (
    PermissionSession,
    ToolApprovalDecision,
    ToolApprovalRequest,
)
from awesome_agent.core.tools.registry import (
    MAX_REGISTERED_TOOL_CATALOG_BYTES,
    MAX_REGISTERED_TOOLS,
    DuplicateToolName,
    RegisteredTool,
    ToolRegistry,
    ToolRegistryLimitError,
)
from awesome_agent.core.workspace import resolve_workspace


class EmptyArguments(BaseModel):
    pass


async def handler(arguments: BaseModel, context: object) -> ToolOutput:
    return ToolOutput(content="ok")


class EchoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    text: str


def execution_context(
    tmp_path: Path,
) -> tuple[ToolExecutionContext, CollectingEventSink, "CollectingActivityWriter"]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)
    sink = CollectingEventSink()
    writer = CollectingActivityWriter()
    ticks = iter((1.0, 1.125, 2.0, 2.5, 3.0, 3.25))
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
        activity_writer=writer,
        monotonic=lambda: next(ticks),
    )
    return context, sink, writer


class CollectingActivityWriter(ToolActivityWriter):
    def __init__(self) -> None:
        self.activities: dict[tuple[str, str], ToolActivityDraft] = {}

    async def finalize(self, activity: ToolActivityDraft) -> None:
        self.activities.setdefault((activity.operation_id, activity.call_id), activity)


class FailingActivityWriter(ToolActivityWriter):
    def __init__(self) -> None:
        self.calls = 0

    async def finalize(self, activity: ToolActivityDraft) -> None:
        del activity
        self.calls += 1
        raise RuntimeError("audit storage unavailable")


class BlockingActivityWriter(ToolActivityWriter):
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.activities: dict[tuple[str, str], ToolActivityDraft] = {}

    async def finalize(self, activity: ToolActivityDraft) -> None:
        self.entered.set()
        await self.release.wait()
        self.activities.setdefault((activity.operation_id, activity.call_id), activity)


class FailingToolTerminalSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.terminal_attempts = 0

    async def emit(self, event: EventEnvelope) -> None:
        if event.event_type is EventType.TOOL_CANCELLED:
            self.terminal_attempts += 1
            raise BrokenPipeError("protocol output closed")
        await super().emit(event)


class BlockingToolTerminalSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.terminal_entered = asyncio.Event()
        self.release_terminal = asyncio.Event()
        self.terminal_attempts = 0

    async def emit(self, event: EventEnvelope) -> None:
        await super().emit(event)
        if event.event_type is EventType.TOOL_CANCELLED:
            self.terminal_attempts += 1
            self.terminal_entered.set()
            await self.release_terminal.wait()


class PreDeliveryBlockingToolTerminalSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.terminal_entered = asyncio.Event()
        self.release_terminal = asyncio.Event()
        self.terminal_attempts = 0

    async def emit(self, event: EventEnvelope) -> None:
        if event.event_type in {
            EventType.TOOL_COMPLETED,
            EventType.TOOL_FAILED,
        }:
            self.terminal_attempts += 1
            self.terminal_entered.set()
            await self.release_terminal.wait()
        await super().emit(event)


def echo_registry(
    handler_override: ToolHandler | None = None,
    *,
    capability: str = "workspace.read",
) -> ToolRegistry:
    async def echo(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        assert isinstance(arguments, EchoArguments)
        return ToolOutput(content=arguments.text)

    registry = ToolRegistry()
    registry.register(
        spec=ToolSpec(
            name="echo",
            description="Echo text",
            input_schema=EchoArguments.model_json_schema(),
            capability=capability,
            read_only=True,
        ),
        input_model=EchoArguments,
        handler=echo if handler_override is None else handler_override,
    )
    return registry


@pytest.mark.asyncio
async def test_executor_asks_before_write_and_executes_handler_only_once(
    tmp_path: Path,
) -> None:
    calls = 0
    approvals: list[ToolApprovalRequest] = []

    async def write_handler(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        nonlocal calls
        calls += 1
        return ToolOutput(content="written")

    async def approve(request: ToolApprovalRequest) -> ToolApprovalDecision:
        assert calls == 0
        approvals.append(request)
        return ToolApprovalDecision.ALLOW_ONCE

    context, _, _ = execution_context(tmp_path)
    context = replace(
        context,
        permission_session=PermissionSession(),
        approval_resolver=approve,
    )
    executor = ToolExecutor(echo_registry(write_handler, capability="workspace.write"))

    result = await executor.execute(
        ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "ok"}),
        context=context,
    )

    assert result.status is ToolStatus.SUCCESS
    assert calls == 1
    assert len(approvals) == 1
    assert approvals[0].capability == "workspace.write"


@pytest.mark.asyncio
async def test_denied_write_never_executes_handler(tmp_path: Path) -> None:
    calls = 0

    async def write_handler(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        nonlocal calls
        calls += 1
        return ToolOutput(content="unreachable")

    async def deny(request: ToolApprovalRequest) -> ToolApprovalDecision:
        return ToolApprovalDecision.DENY

    context, _, _ = execution_context(tmp_path)
    context = replace(context, approval_resolver=deny)
    executor = ToolExecutor(echo_registry(write_handler, capability="workspace.write"))

    result = await executor.execute(
        ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "ok"}),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED
    assert calls == 0


@pytest.mark.asyncio
async def test_thread_write_grant_suppresses_later_write_approval(
    tmp_path: Path,
) -> None:
    approvals: list[ToolApprovalRequest] = []

    async def approve(request: ToolApprovalRequest) -> ToolApprovalDecision:
        approvals.append(request)
        return ToolApprovalDecision.ALLOW_THREAD_WRITES

    context, _, _ = execution_context(tmp_path)
    context = replace(context, approval_resolver=approve)
    executor = ToolExecutor(echo_registry(capability="workspace.write"))

    for call_id in ("call_1", "call_2"):
        result = await executor.execute(
            ToolRequest(
                call_id=call_id,
                tool_name="echo",
                arguments={"text": "ok"},
            ),
            context=context,
        )
        assert result.status is ToolStatus.SUCCESS

    assert len(approvals) == 1


def test_registry_rejects_duplicates_and_lists_sorted_specs() -> None:
    registry = ToolRegistry()
    for name in ("grep", "ls"):
        registry.register(
            spec=ToolSpec(
                name=name,
                description=name,
                input_schema=EmptyArguments.model_json_schema(),
                capability="workspace.read",
                read_only=True,
            ),
            input_model=EmptyArguments,
            handler=handler,
        )

    assert [spec.name for spec in registry.specifications()] == ["grep", "ls"]
    registered = registry.resolve("ls")
    assert registered is not None
    with pytest.raises(DuplicateToolName):
        registry.register(
            spec=registered.spec,
            input_model=EmptyArguments,
            handler=handler,
        )


def test_tool_names_allow_only_baseline_and_reserved_namespaces() -> None:
    for name in (
        "read_file",
        "mcp.fixture.search-code",
        "user.package.tool-name",
    ):
        assert (
            ToolSpec(
                name=name,
                description=name,
                input_schema={},
                capability="workspace.read",
                read_only=True,
            ).name
            == name
        )

    for name in (
        "MCP.fixture.tool",
        "mcp.fixture",
        "mcp.fixture.bad.tool",
        "other.package.tool",
        "bad-name",
    ):
        with pytest.raises(ValidationError):
            ToolSpec(
                name=name,
                description=name,
                input_schema={},
                capability="workspace.read",
                read_only=True,
            )


def test_registry_replaces_one_namespace_atomically() -> None:
    registry = echo_registry()

    def registered(name: str) -> RegisteredTool:
        return RegisteredTool(
            ToolSpec(
                name=name,
                description=name,
                input_schema={},
                capability="workspace.write",
                read_only=False,
            ),
            EmptyArguments,
            handler,
        )

    registry.replace_namespace("mcp.one", (registered("mcp.one.first"),))
    registry.replace_namespace("mcp.two", (registered("mcp.two.second"),))
    registry.replace_namespace("mcp.one", (registered("mcp.one.replaced"),))

    assert [spec.name for spec in registry.specifications()] == [
        "echo",
        "mcp.one.replaced",
        "mcp.two.second",
    ]

    with pytest.raises(ValueError):
        registry.replace_namespace("mcp.one", (registered("mcp.two.invalid"),))
    assert registry.resolve("mcp.one.replaced") is not None

    registry.remove_namespace("mcp.one")
    assert registry.resolve("echo") is not None
    assert registry.resolve("mcp.two.second") is not None
    assert registry.resolve("mcp.one.replaced") is None


def test_registry_replaces_an_exact_tool_set_without_touching_mcp_namespace() -> None:
    registry = echo_registry()

    def registered(name: str) -> RegisteredTool:
        return RegisteredTool(
            ToolSpec(
                name=name,
                description=name,
                input_schema={},
                capability="workspace.read",
                read_only=True,
            ),
            EmptyArguments,
            handler,
        )

    mcp_tool = registered("mcp.fixture.search")
    registry.replace_namespace("mcp.fixture", (mcp_tool,))
    managed_names = ("memory_list", "memory_add")

    registry.replace_exact_set(
        managed_names,
        (registered("memory_list"), registered("memory_add")),
    )

    assert registry.resolve("mcp.fixture.search") is mcp_tool
    assert registry.resolve("echo") is not None
    assert registry.resolve("memory_list") is not None
    assert registry.resolve("memory_add") is not None

    registry.replace_exact_set(managed_names, ())

    assert registry.resolve("mcp.fixture.search") is mcp_tool
    assert registry.resolve("echo") is not None
    assert registry.resolve("memory_list") is None
    assert registry.resolve("memory_add") is None


@pytest.mark.parametrize("base_count", [125, 126, 127, 128])
def test_registry_rejects_exact_set_over_aggregate_limit_without_partial_change(
    base_count: int,
) -> None:
    registry = ToolRegistry()

    def registered(name: str) -> RegisteredTool:
        return RegisteredTool(
            ToolSpec(
                name=name,
                description="bounded",
                input_schema={},
                capability="workspace.read",
                read_only=True,
            ),
            EmptyArguments,
            handler,
        )

    for index in range(base_count - 1):
        item = registered(f"base_{index}")
        registry.register(
            spec=item.spec,
            input_model=item.input_model,
            handler=item.handler,
        )
    mcp_tool = registered("mcp.fixture.search")
    registry.replace_namespace("mcp.fixture", (mcp_tool,))
    before = registry.specifications()

    with pytest.raises(ToolRegistryLimitError, match="128-tool aggregate limit"):
        registry.replace_exact_set(
            ("memory_list", "memory_add", "memory_replace", "memory_remove"),
            tuple(
                registered(name)
                for name in (
                    "memory_list",
                    "memory_add",
                    "memory_replace",
                    "memory_remove",
                )
            ),
        )

    assert registry.specifications() == before
    assert registry.resolve("mcp.fixture.search") is mcp_tool
    assert all(
        registry.resolve(name) is None
        for name in (
            "memory_list",
            "memory_add",
            "memory_replace",
            "memory_remove",
        )
    )


def test_registry_accepts_exact_set_at_aggregate_limit() -> None:
    registry = ToolRegistry()

    def registered(name: str) -> RegisteredTool:
        return RegisteredTool(
            ToolSpec(
                name=name,
                description="bounded",
                input_schema={},
                capability="workspace.read",
                read_only=True,
            ),
            EmptyArguments,
            handler,
        )

    for index in range(MAX_REGISTERED_TOOLS - 4):
        item = registered(f"base_{index}")
        registry.register(
            spec=item.spec,
            input_model=item.input_model,
            handler=item.handler,
        )
    memory_tools = tuple(
        registered(name)
        for name in (
            "memory_list",
            "memory_add",
            "memory_replace",
            "memory_remove",
        )
    )

    registry.replace_exact_set(
        tuple(tool.spec.name for tool in memory_tools),
        memory_tools,
    )

    assert len(registry.specifications()) == MAX_REGISTERED_TOOLS
    assert all(registry.resolve(tool.spec.name) is tool for tool in memory_tools)


def test_registry_rejects_invalid_exact_set_contracts_without_mutation() -> None:
    registry = echo_registry()
    before = registry.specifications()
    valid_spec = ToolSpec(
        name="memory_list",
        description="List memory.",
        input_schema={},
        capability="memory.read",
        read_only=True,
    )
    invalid_spec = ToolSpec.model_construct(
        name="memory_list",
        description="",
        input_schema={},
        capability="memory.read",
        read_only=True,
        display_metadata={},
    )
    candidates = (
        RegisteredTool(invalid_spec, EmptyArguments, handler),
        RegisteredTool(valid_spec, EmptyArguments, cast(ToolHandler, None)),
        RegisteredTool(
            valid_spec,
            EmptyArguments,
            handler,
            cancellation_grace_seconds=0.0,
        ),
        RegisteredTool(
            valid_spec,
            EmptyArguments,
            handler,
            cancellation_grace_seconds=cast(float, "invalid"),
        ),
    )

    for candidate in candidates:
        with pytest.raises(ToolRegistryLimitError):
            registry.replace_exact_set(("memory_list",), (candidate,))
        assert registry.specifications() == before


def test_registry_rejects_duplicate_or_unmanaged_exact_set_tools() -> None:
    registry = echo_registry()

    def registered(name: str) -> RegisteredTool:
        return RegisteredTool(
            ToolSpec(
                name=name,
                description=name,
                input_schema={},
                capability="workspace.read",
                read_only=True,
            ),
            EmptyArguments,
            handler,
        )

    before = registry.specifications()
    duplicate = registered("memory_list")
    with pytest.raises(DuplicateToolName, match="memory_list"):
        registry.replace_exact_set(
            ("memory_list",),
            (duplicate, duplicate),
        )
    with pytest.raises(ValueError, match="outside the managed exact set"):
        registry.replace_exact_set(
            ("memory_list",),
            (registered("memory_add"),),
        )

    assert registry.specifications() == before


def test_registry_rejects_aggregate_tool_count_without_partial_replacement() -> None:
    registry = ToolRegistry()

    def registered(name: str) -> RegisteredTool:
        return RegisteredTool(
            ToolSpec(
                name=name,
                description="bounded",
                input_schema={},
                capability="workspace.read",
                read_only=True,
            ),
            EmptyArguments,
            handler,
        )

    for index in range(MAX_REGISTERED_TOOLS - 1):
        item = registered(f"base_{index}")
        registry.register(
            spec=item.spec,
            input_model=item.input_model,
            handler=item.handler,
        )
    registry.replace_namespace("mcp.one", (registered("mcp.one.previous"),))

    with pytest.raises(ToolRegistryLimitError) as raised:
        registry.replace_namespace(
            "mcp.one",
            (
                registered("mcp.one.first"),
                registered("mcp.one.second"),
            ),
        )

    assert str(raised.value) == "Tool registry exceeds the 128-tool aggregate limit"
    assert registry.resolve("mcp.one.previous") is not None
    assert registry.resolve("mcp.one.first") is None
    assert len(registry.specifications()) == MAX_REGISTERED_TOOLS


def test_registry_rejects_aggregate_bytes_without_mutating_existing_tools() -> None:
    registry = echo_registry()
    oversized = RegisteredTool(
        ToolSpec(
            name="mcp.one.large",
            description="bounded",
            input_schema={
                "type": "string",
                "description": "x" * MAX_REGISTERED_TOOL_CATALOG_BYTES,
            },
            capability="mcp.invoke",
            read_only=False,
        ),
        EmptyArguments,
        handler,
    )

    with pytest.raises(ToolRegistryLimitError) as raised:
        registry.replace_namespace("mcp.one", (oversized,))

    assert str(raised.value) == (
        "Tool registry exceeds the 1 MiB aggregate catalog limit"
    )
    assert registry.resolve("echo") is not None
    assert registry.resolve("mcp.one.large") is None


def test_registry_rejects_non_utf8_contract_without_mutating_snapshot() -> None:
    registry = echo_registry()

    with pytest.raises(ToolRegistryLimitError, match="unsafe catalog contract"):
        registry.register(
            spec=ToolSpec.model_construct(
                name="unsafe",
                description="\ud800",
                input_schema={},
                capability="workspace.read",
                read_only=True,
                display_metadata={},
            ),
            input_model=EmptyArguments,
            handler=handler,
        )

    assert [spec.name for spec in registry.specifications()] == ["echo"]


@pytest.mark.parametrize("grace", [0.0, -1.0, float("inf"), float("nan")])
def test_registry_rejects_invalid_handler_cancellation_grace(grace: float) -> None:
    registry = echo_registry()

    with pytest.raises(ToolRegistryLimitError, match="invalid cancellation grace"):
        registry.register(
            spec=ToolSpec(
                name="invalid_grace",
                description="Invalid cancellation contract",
                input_schema={},
                capability="workspace.read",
                read_only=True,
            ),
            input_model=EmptyArguments,
            handler=handler,
            cancellation_grace_seconds=grace,
        )

    assert [spec.name for spec in registry.specifications()] == ["echo"]


@pytest.mark.asyncio
async def test_executor_emits_success_events(tmp_path: Path) -> None:
    context, sink, writer = execution_context(tmp_path)
    executor = ToolExecutor(echo_registry())

    result = await executor.execute(
        ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "ok"}),
        context=context,
    )

    assert result.status is ToolStatus.SUCCESS
    assert result.content == "ok"
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
    ]
    [activity] = writer.activities.values()
    assert activity.thread_id == "thread_1"
    assert activity.turn_id == "turn_1"
    assert activity.origin is ToolExecutionOrigin.AGENT
    assert activity.duration_ms == 125
    assert activity.change_set_id is None
    assert activity.input_summary == "arguments: text"
    assert activity.result_summary == "Completed"
    started, completed = sink.events
    assert started.payload.verb == "Echo"  # type: ignore[union-attr]
    assert started.payload.target is None  # type: ignore[union-attr]
    assert completed.payload.outcome == "Completed"  # type: ignore[union-attr]
    assert completed.payload.summary == "Completed"  # type: ignore[union-attr]
    assert completed.payload.duration_ms == 125  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"text": 1},
        {"text": "ok", "unexpected": True},
    ],
)
async def test_executor_normalizes_invalid_arguments(
    tmp_path: Path,
    arguments: dict[str, JsonValue],
) -> None:
    context, sink, writer = execution_context(tmp_path)
    executor = ToolExecutor(echo_registry())

    result = await executor.execute(
        ToolRequest(call_id="call_1", tool_name="echo", arguments=arguments),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_FAILED,
    ]
    assert next(iter(writer.activities.values())).error_code == "invalid_arguments"


@pytest.mark.asyncio
async def test_executor_normalizes_expected_failure(tmp_path: Path) -> None:
    async def missing(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        raise ExpectedToolFailure(
            ToolErrorCode.NOT_FOUND,
            "Path was not found.",
            retryable=True,
        )

    context, sink, writer = execution_context(tmp_path)
    executor = ToolExecutor(echo_registry(missing))

    result = await executor.execute(
        ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "x"}),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.NOT_FOUND
    assert result.error.retryable is True
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_FAILED,
    ]
    assert next(iter(writer.activities.values())).result_summary == "not_found"


@pytest.mark.asyncio
async def test_executor_normalizes_timeout(tmp_path: Path) -> None:
    waiting = asyncio.Event()

    async def wait_forever(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        await waiting.wait()
        return ToolOutput(content="unreachable")

    context, sink, writer = execution_context(tmp_path)
    executor = ToolExecutor(echo_registry(wait_forever), timeout_seconds=0.01)

    result = await executor.execute(
        ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "x"}),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TIMEOUT
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_FAILED,
    ]
    assert next(iter(writer.activities.values())).error_code == "timeout"


@pytest.mark.asyncio
async def test_executor_records_cancellation_then_reraises(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    finished = asyncio.Event()

    async def cancellable(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()
        raise AssertionError("Cancellation did not stop the handler.")

    context, sink, writer = execution_context(tmp_path)
    executor = ToolExecutor(echo_registry(cancellable), timeout_seconds=30.0)
    task = asyncio.create_task(
        executor.execute(
            ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "x"}),
            context=context,
        )
    )
    await started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.done()
    assert finished.is_set()
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_CANCELLED,
    ]
    [activity] = writer.activities.values()
    assert activity.outcome == "cancelled"
    assert activity.error_code == "cancelled"


@pytest.mark.asyncio
async def test_executor_preserves_cancellation_when_audit_finalization_fails(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()

    async def cancellable(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        del arguments, context
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("Cancellation did not stop the handler.")

    context, sink, _ = execution_context(tmp_path)
    writer = FailingActivityWriter()
    context = replace(context, activity_writer=writer)
    executor = ToolExecutor(echo_registry(cancellable), timeout_seconds=30.0)
    task = asyncio.create_task(
        executor.execute(
            ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "x"}),
            context=context,
        )
    )
    await started.wait()

    task.cancel("original cancellation")
    with pytest.raises(asyncio.CancelledError) as captured:
        await task

    assert captured.value.args == ("original cancellation",)
    assert writer.calls == 1
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_CANCELLED,
    ]


@pytest.mark.asyncio
async def test_executor_preserves_cancellation_when_terminal_delivery_fails(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()

    async def cancellable(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        del arguments, context
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("Cancellation did not stop the handler.")

    context, _, writer = execution_context(tmp_path)
    sink = FailingToolTerminalSink()
    context = replace(
        context,
        emitter=EventEmitter(
            session_id="session_1",
            workspace_key=context.workspace.key,
            sink=sink,
        ),
    )
    executor = ToolExecutor(echo_registry(cancellable), timeout_seconds=30.0)
    task = asyncio.create_task(
        executor.execute(
            ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "x"}),
            context=context,
        )
    )
    await started.wait()

    task.cancel("original cancellation")
    with pytest.raises(asyncio.CancelledError) as captured:
        await task

    assert captured.value.args == ("original cancellation",)
    assert sink.terminal_attempts == 1
    assert len(writer.activities) == 1


@pytest.mark.asyncio
async def test_executor_repeated_cancel_finishes_terminalization_once(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()

    async def cancellable(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        del arguments, context
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("Cancellation did not stop the handler.")

    context, _, writer = execution_context(tmp_path)
    sink = BlockingToolTerminalSink()
    context = replace(
        context,
        emitter=EventEmitter(
            session_id="session_1",
            workspace_key=context.workspace.key,
            sink=sink,
        ),
    )
    executor = ToolExecutor(echo_registry(cancellable), timeout_seconds=30.0)
    task = asyncio.create_task(
        executor.execute(
            ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "x"}),
            context=context,
        )
    )
    await started.wait()

    task.cancel("original cancellation")
    await sink.terminal_entered.wait()
    task.cancel("second cancellation")
    sink.release_terminal.set()
    with pytest.raises(asyncio.CancelledError) as captured:
        await task

    assert captured.value.args == ("original cancellation",)
    assert sink.terminal_attempts == 1
    assert [event.event_type for event in sink.events].count(
        EventType.TOOL_CANCELLED
    ) == 1
    assert len(writer.activities) == 1


@pytest.mark.asyncio
async def test_executor_bounds_cancelled_terminal_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def cancellable(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        del arguments, context
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("Cancellation did not stop the handler.")

    context, _, writer = execution_context(tmp_path)
    sink = BlockingToolTerminalSink()
    context = replace(
        context,
        emitter=EventEmitter(
            session_id="session_1",
            workspace_key=context.workspace.key,
            sink=sink,
        ),
    )
    monkeypatch.setattr(
        "awesome_agent.core.tools.executor._TERMINAL_CANCELLATION_CLEANUP_SECONDS",
        0.01,
    )
    executor = ToolExecutor(echo_registry(cancellable), timeout_seconds=30.0)
    task = asyncio.create_task(
        executor.execute(
            ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "x"}),
            context=context,
        )
    )
    await started.wait()

    task.cancel("original cancellation")
    await sink.terminal_entered.wait()
    with pytest.raises(asyncio.CancelledError) as captured:
        await asyncio.wait_for(task, timeout=1)

    assert captured.value.args == ("original cancellation",)
    assert sink.terminal_attempts == 1
    assert [event.event_type for event in sink.events].count(
        EventType.TOOL_CANCELLED
    ) == 1
    assert len(writer.activities) == 1


@pytest.mark.asyncio
async def test_executor_waits_for_cancelled_activity_before_terminal_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler_entered = asyncio.Event()

    async def cancellable(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        del arguments, context
        handler_entered.set()
        await asyncio.Event().wait()
        raise AssertionError("Cancellation did not stop the handler.")

    context, sink, _ = execution_context(tmp_path)
    writer = BlockingActivityWriter()
    context = replace(context, activity_writer=writer)
    monkeypatch.setattr(
        "awesome_agent.core.tools.executor._TERMINAL_CANCELLATION_CLEANUP_SECONDS",
        0.01,
    )
    executor = ToolExecutor(echo_registry(cancellable), timeout_seconds=30.0)
    task = asyncio.create_task(
        executor.execute(
            ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "x"}),
            context=context,
        )
    )
    await handler_entered.wait()

    task.cancel("cancel while handler runs")
    await writer.entered.wait()
    await asyncio.sleep(0.03)

    assert not task.done()
    assert [event.event_type for event in sink.events] == [EventType.TOOL_STARTED]

    writer.release.set()
    with pytest.raises(asyncio.CancelledError) as captured:
        await asyncio.wait_for(task, timeout=1)

    assert captured.value.args == ("cancel while handler runs",)
    [activity] = writer.activities.values()
    assert activity.outcome == "cancelled"
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_CANCELLED,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_outcome", "terminal_type", "activity_outcome"),
    (
        ("success", EventType.TOOL_COMPLETED, "success"),
        ("error", EventType.TOOL_FAILED, "error"),
    ),
)
async def test_executor_finishes_handler_outcome_when_cancelled_during_terminal_emit(
    tmp_path: Path,
    handler_outcome: str,
    terminal_type: EventType,
    activity_outcome: str,
) -> None:
    handler_calls = 0

    async def completed_handler(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        nonlocal handler_calls
        del arguments, context
        handler_calls += 1
        if handler_outcome == "error":
            raise ExpectedToolFailure(ToolErrorCode.NOT_FOUND, "Missing.")
        return ToolOutput(content="ok")

    context, _, writer = execution_context(tmp_path)
    sink = PreDeliveryBlockingToolTerminalSink()
    context = replace(
        context,
        emitter=EventEmitter(
            session_id="session_1",
            workspace_key=context.workspace.key,
            sink=sink,
        ),
    )
    executor = ToolExecutor(echo_registry(completed_handler))
    task = asyncio.create_task(
        executor.execute(
            ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "x"}),
            context=context,
        )
    )
    await sink.terminal_entered.wait()

    task.cancel("cancel during terminal emit")
    await asyncio.sleep(0)
    sink.release_terminal.set()
    with pytest.raises(asyncio.CancelledError) as captured:
        await task

    assert captured.value.args == ("cancel during terminal emit",)
    assert handler_calls == 1
    assert sink.terminal_attempts == 1
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        terminal_type,
    ]
    [activity] = writer.activities.values()
    assert activity.outcome == activity_outcome


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_outcome", "terminal_type", "activity_outcome"),
    (
        ("success", EventType.TOOL_COMPLETED, "success"),
        ("error", EventType.TOOL_FAILED, "error"),
    ),
)
async def test_executor_preserves_handler_outcome_when_cancelled_during_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler_outcome: str,
    terminal_type: EventType,
    activity_outcome: str,
) -> None:
    async def completed_handler(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        del arguments, context
        if handler_outcome == "error":
            raise ExpectedToolFailure(ToolErrorCode.NOT_FOUND, "Missing.")
        return ToolOutput(content="ok")

    context, sink, _ = execution_context(tmp_path)
    writer = BlockingActivityWriter()
    context = replace(context, activity_writer=writer)
    monkeypatch.setattr(
        "awesome_agent.core.tools.executor._TERMINAL_CANCELLATION_CLEANUP_SECONDS",
        0.01,
    )
    executor = ToolExecutor(echo_registry(completed_handler))
    task = asyncio.create_task(
        executor.execute(
            ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "x"}),
            context=context,
        )
    )
    await writer.entered.wait()

    task.cancel("cancel during audit")
    await asyncio.sleep(0.03)

    assert not task.done()
    assert [event.event_type for event in sink.events] == [EventType.TOOL_STARTED]

    writer.release.set()
    with pytest.raises(asyncio.CancelledError) as captured:
        await asyncio.wait_for(task, timeout=1)

    assert captured.value.args == ("cancel during audit",)
    [activity] = writer.activities.values()
    assert activity.outcome == activity_outcome
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        terminal_type,
    ]


@pytest.mark.asyncio
async def test_executor_bounds_unresponsive_success_terminal_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler_calls = 0

    async def completed_handler(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        nonlocal handler_calls
        del arguments, context
        handler_calls += 1
        return ToolOutput(content="ok")

    context, _, writer = execution_context(tmp_path)
    sink = PreDeliveryBlockingToolTerminalSink()
    context = replace(
        context,
        emitter=EventEmitter(
            session_id="session_1",
            workspace_key=context.workspace.key,
            sink=sink,
        ),
    )
    monkeypatch.setattr(
        "awesome_agent.core.tools.executor._TERMINAL_CANCELLATION_CLEANUP_SECONDS",
        0.01,
    )
    executor = ToolExecutor(echo_registry(completed_handler))
    task = asyncio.create_task(
        executor.execute(
            ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "x"}),
            context=context,
        )
    )
    await sink.terminal_entered.wait()

    task.cancel("cancel during terminal emit")
    with pytest.raises(asyncio.CancelledError) as captured:
        await asyncio.wait_for(task, timeout=1)

    assert captured.value.args == ("cancel during terminal emit",)
    assert handler_calls == 1
    assert sink.terminal_attempts == 1
    assert [event.event_type for event in sink.events] == [EventType.TOOL_STARTED]
    [activity] = writer.activities.values()
    assert activity.outcome == "success"
    assert "terminal event delivery is uncertain" in caplog.text


@pytest.mark.asyncio
async def test_executor_does_not_swallow_audit_failure_on_normal_completion(
    tmp_path: Path,
) -> None:
    context, sink, _ = execution_context(tmp_path)
    writer = FailingActivityWriter()
    context = replace(context, activity_writer=writer)
    executor = ToolExecutor(echo_registry())

    with pytest.raises(ToolInvariantError, match="audit finalization"):
        await executor.execute(
            ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "x"}),
            context=context,
        )

    assert writer.calls == 1
    assert [event.event_type for event in sink.events] == [EventType.TOOL_STARTED]


@pytest.mark.asyncio
async def test_executor_records_safe_invariant_failure_before_reraising(
    tmp_path: Path,
) -> None:
    async def explode(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        raise RuntimeError("raw traceback secret")

    context, sink, writer = execution_context(tmp_path)
    executor = ToolExecutor(echo_registry(explode))

    with pytest.raises(ToolInvariantError):
        await executor.execute(
            ToolRequest(
                call_id="call_1",
                tool_name="echo",
                arguments={"text": "raw input secret"},
            ),
            context=context,
        )

    [activity] = writer.activities.values()
    serialized = activity.model_dump_json()
    assert activity.error_code == "execution_failed"
    assert "raw input secret" not in serialized
    assert "raw traceback secret" not in serialized
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_FAILED,
    ]


def test_execution_context_enforces_origin_turn_invariant(tmp_path: Path) -> None:
    context, _, _ = execution_context(tmp_path)

    with pytest.raises(ValueError, match="agent"):
        replace(context, turn_id=None)
    with pytest.raises(ValueError, match="direct"):
        replace(context, origin=ToolExecutionOrigin.DIRECT)

    direct = replace(
        context,
        origin=ToolExecutionOrigin.DIRECT,
        turn_id=None,
    )
    assert direct.thread_id == "thread_1"
