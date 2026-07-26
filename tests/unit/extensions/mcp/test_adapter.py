from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from jsonschema.protocols import Validator
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from pydantic import JsonValue

import awesome_agent.extensions.mcp.adapter as mcp_adapter
from awesome_agent.core.events import CollectingEventSink, EventEmitter, EventType
from awesome_agent.core.tools import (
    PermissionMode,
    PermissionSession,
    ToolActivityDraft,
    ToolActivityWriter,
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolRequest,
    ToolStatus,
)
from awesome_agent.core.tools.executor import ToolExecutor
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.extensions.mcp import McpCallUncertain, McpUnavailable
from awesome_agent.extensions.mcp.adapter import McpToolAdapter
from awesome_agent.extensions.mcp.catalog import McpCatalog, compile_mcp_catalog


class ActivityWriter(ToolActivityWriter):
    def __init__(self) -> None:
        self.activities: list[ToolActivityDraft] = []

    def finalize(self, activity: ToolActivityDraft) -> None:
        self.activities.append(activity)


class FakeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.result = CallToolResult(
            content=[TextContent(type="text", text="hello")],
        )
        self.uncertain = False
        self._catalog = compile_mcp_catalog(
            (echo_tool(),), server_id="fixture", generation=1
        )

    def catalog(self, server_id: str) -> McpCatalog:
        assert server_id == "fixture"
        return self._catalog

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, object],
        *,
        generation: int,
    ) -> CallToolResult:
        if generation != self._catalog.generation:
            raise McpUnavailable("stale catalog")
        self.calls.append((server_id, tool_name, arguments))
        if self.uncertain:
            raise McpCallUncertain("uncertain secret")
        return self.result

    def replace_catalog(self, catalog: McpCatalog) -> None:
        self._catalog = catalog


async def approve(_: ToolApprovalRequest) -> ToolApprovalDecision:
    return ToolApprovalDecision.ALLOW_ONCE


def context(
    tmp_path: Path,
    *,
    sink: CollectingEventSink | None = None,
    activity_writer: ActivityWriter | None = None,
) -> ToolExecutionContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    identity = resolve_workspace(workspace)
    ticks = iter((1.0, 1.1))
    return ToolExecutionContext(
        workspace=identity,
        thread_id="thread",
        operation_id="operation",
        turn_id="turn",
        origin=ToolExecutionOrigin.AGENT,
        emitter=EventEmitter(
            session_id="session",
            workspace_key=identity.key,
            sink=CollectingEventSink() if sink is None else sink,
        ),
        activity_writer=(
            ActivityWriter() if activity_writer is None else activity_writer
        ),
        monotonic=lambda: next(ticks),
        permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
        approval_resolver=approve,
    )


def publish_catalog(manager: FakeManager, registry: ToolRegistry) -> None:
    adapter = McpToolAdapter(manager, "fixture")
    registry.replace_namespace(
        "mcp.fixture",
        adapter.registered_tools(manager.catalog("fixture")),
    )


def echo_tool() -> Tool:
    return Tool(
        name="echo",
        description="Echo text",
        inputSchema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        annotations=ToolAnnotations(readOnlyHint=True),
    )


@pytest.mark.asyncio
async def test_adapter_registers_and_executes_only_through_shared_executor(
    tmp_path: Path,
) -> None:
    manager = FakeManager()
    registry = ToolRegistry()
    publish_catalog(manager, registry)
    registered = registry.resolve("mcp.fixture.echo")
    assert registered is not None
    assert registered.spec.read_only is False
    assert registered.timeout_resolver is not None
    assert (
        registered.timeout_resolver(
            registered.input_model.model_validate({"text": "hello"})
        )
        == 40.0
    )

    executor = ToolExecutor(registry)
    result = await executor.execute(
        ToolRequest(
            call_id="call",
            tool_name="mcp.fixture.echo",
            arguments={"text": "hello"},
        ),
        context=context(tmp_path),
    )

    assert result.status is ToolStatus.SUCCESS
    assert result.content == "hello"
    assert result.metadata["external_side_effect"] == "unknown"
    assert manager.calls == [("fixture", "echo", {"text": "hello"})]


@pytest.mark.asyncio
async def test_adapter_validates_arguments_and_normalizes_error_results(
    tmp_path: Path,
) -> None:
    manager = FakeManager()
    registry = ToolRegistry()
    publish_catalog(manager, registry)
    executor = ToolExecutor(registry)

    invalid = await executor.execute(
        ToolRequest(
            call_id="invalid",
            tool_name="mcp.fixture.echo",
            arguments={"extra": True},
        ),
        context=context(tmp_path),
    )
    assert invalid.error is not None
    assert invalid.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert manager.calls == []

    manager.result = CallToolResult(
        content=[TextContent(type="text", text="safe failure")],
        isError=True,
    )
    failed = await executor.execute(
        ToolRequest(
            call_id="failed",
            tool_name="mcp.fixture.echo",
            arguments={"text": "hello"},
        ),
        context=context(tmp_path),
    )
    assert failed.error is not None
    assert failed.error.code is ToolErrorCode.EXECUTION_FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"value": float("nan")},
        {"value": [float("inf")]},
        {"value": {"nested": float("-inf")}},
    ],
    ids=("nan", "positive-infinity", "nested-negative-infinity"),
)
async def test_adapter_rejects_non_strict_json_before_approval_or_remote_call(
    tmp_path: Path,
    arguments: dict[str, JsonValue],
) -> None:
    permissive_tool = Tool(
        name="echo",
        description="Accept any JSON object",
        inputSchema={"type": "object"},
    )
    manager = FakeManager()
    manager.replace_catalog(
        compile_mcp_catalog(
            (permissive_tool,),
            server_id="fixture",
            generation=1,
        )
    )
    registry = ToolRegistry()
    publish_catalog(manager, registry)
    approvals: list[ToolApprovalRequest] = []
    sink = CollectingEventSink()
    activity_writer = ActivityWriter()

    async def record_approval(request: ToolApprovalRequest) -> ToolApprovalDecision:
        approvals.append(request)
        return ToolApprovalDecision.ALLOW_ONCE

    execution_context = replace(
        context(
            tmp_path,
            sink=sink,
            activity_writer=activity_writer,
        ),
        approval_resolver=record_approval,
    )
    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="non-strict-json",
            tool_name="mcp.fixture.echo",
            arguments=arguments,
        ),
        context=execution_context,
    )

    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.message == "Tool arguments did not match the schema."
    assert approvals == []
    assert manager.calls == []
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_FAILED,
    ]
    assert len(activity_writer.activities) == 1
    assert activity_writer.activities[0].outcome == "error"
    assert activity_writer.activities[0].error_code == ToolErrorCode.INVALID_ARGUMENTS


@pytest.mark.asyncio
async def test_adapter_never_replays_an_uncertain_external_call(tmp_path: Path) -> None:
    manager = FakeManager()
    manager.uncertain = True
    registry = ToolRegistry()
    publish_catalog(manager, registry)

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="uncertain",
            tool_name="mcp.fixture.echo",
            arguments={"text": "once"},
        ),
        context=context(tmp_path),
    )

    assert result.error is not None
    assert result.error.code is ToolErrorCode.UNCERTAIN_OUTCOME
    assert result.error.retryable is False
    assert len(manager.calls) == 1
    assert "secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_adapter_uses_compiled_composition_and_reference_validator(
    tmp_path: Path,
) -> None:
    schema_tool = Tool(
        name="echo",
        description="Echo text",
        inputSchema={
            "$defs": {"long": {"type": "string", "minLength": 3}},
            "type": "object",
            "properties": {
                "text": {
                    "allOf": [
                        {"$ref": "#/$defs/long"},
                        {"pattern": "^[a-z]+$"},
                    ]
                }
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    manager = FakeManager()
    manager.replace_catalog(
        compile_mcp_catalog((schema_tool,), server_id="fixture", generation=4)
    )
    registry = ToolRegistry()
    publish_catalog(manager, registry)
    executor = ToolExecutor(registry)

    invalid = await executor.execute(
        ToolRequest(
            call_id="invalid-composition",
            tool_name="mcp.fixture.echo",
            arguments={"text": "X"},
        ),
        context=context(tmp_path),
    )

    assert invalid.error is not None
    assert invalid.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert manager.calls == []
    assert "pattern" not in invalid.model_dump_json()


@pytest.mark.asyncio
async def test_adapter_validates_and_renders_structured_only_output(
    tmp_path: Path,
) -> None:
    structured_tool = Tool(
        name="echo",
        inputSchema={"type": "object"},
        outputSchema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )
    manager = FakeManager()
    manager.replace_catalog(
        compile_mcp_catalog((structured_tool,), server_id="fixture", generation=5)
    )
    manager.result = CallToolResult(
        content=[],
        structuredContent={"answer": "yes"},
    )
    registry = ToolRegistry()
    publish_catalog(manager, registry)

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="structured",
            tool_name="mcp.fixture.echo",
            arguments={},
        ),
        context=context(tmp_path),
    )

    assert result.status is ToolStatus.SUCCESS
    assert result.content == '{"answer":"yes"}'
    assert len(manager.calls) == 1


@pytest.mark.asyncio
async def test_adapter_rejects_invalid_structured_output_without_schema_leak(
    tmp_path: Path,
) -> None:
    structured_tool = Tool(
        name="echo",
        inputSchema={"type": "object"},
        outputSchema={
            "type": "object",
            "properties": {"secret_field": {"type": "string"}},
            "required": ["secret_field"],
            "additionalProperties": False,
        },
    )
    manager = FakeManager()
    manager.replace_catalog(
        compile_mcp_catalog((structured_tool,), server_id="fixture", generation=5)
    )
    manager.result = CallToolResult(
        content=[],
        structuredContent={"secret_field": 42},
    )
    registry = ToolRegistry()
    publish_catalog(manager, registry)

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="invalid-structured",
            tool_name="mcp.fixture.echo",
            arguments={},
        ),
        context=context(tmp_path),
    )

    assert result.error is not None
    assert result.error.code is ToolErrorCode.EXECUTION_FAILED
    assert result.error.retryable is False
    assert len(manager.calls) == 1
    assert "secret_field" not in result.model_dump_json()

    manager.result = CallToolResult(content=[])
    missing = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="missing-structured",
            tool_name="mcp.fixture.echo",
            arguments={},
        ),
        context=context(tmp_path),
    )
    assert missing.error is not None
    assert missing.error.code is ToolErrorCode.EXECUTION_FAILED
    assert missing.error.retryable is False
    assert len(manager.calls) == 2


@pytest.mark.asyncio
async def test_adapter_bounds_large_output_while_preserving_head_and_tail(
    tmp_path: Path,
) -> None:
    manager = FakeManager()
    manager.result = CallToolResult(
        content=[
            TextContent(type="text", text="a" * 20_000),
            TextContent(type="text", text="b" * 20_000),
        ]
    )
    registry = ToolRegistry()
    publish_catalog(manager, registry)

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="large",
            tool_name="mcp.fixture.echo",
            arguments={"text": "hello"},
        ),
        context=context(tmp_path),
    )

    assert result.status is ToolStatus.SUCCESS
    assert result.content.startswith("a" * 20_000)
    assert result.content.endswith("b" * 5_000)
    assert "11001 characters omitted" in result.content
    assert result.metadata["truncated"] is True


@pytest.mark.asyncio
async def test_adapter_rejects_excessive_content_blocks_before_render(
    tmp_path: Path,
) -> None:
    manager = FakeManager()
    manager.result = CallToolResult(
        content=[
            TextContent(type="text", text="x")
            for _ in range(mcp_adapter._MAX_CONTENT_BLOCKS + 1)
        ]
    )
    registry = ToolRegistry()
    publish_catalog(manager, registry)

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="too-many-blocks",
            tool_name="mcp.fixture.echo",
            arguments={"text": "hello"},
        ),
        context=context(tmp_path),
    )

    assert result.error is not None
    assert result.error.code is ToolErrorCode.EXECUTION_FAILED
    assert result.error.retryable is False
    assert result.error.message == (
        "MCP tool returned content outside safe resource limits."
    )


@pytest.mark.asyncio
async def test_adapter_rejects_non_utf8_text_before_render_with_one_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeManager()
    manager.result = CallToolResult(
        content=[
            TextContent(type="text", text="safe"),
            TextContent(type="text", text="sensitive\ud800payload"),
        ]
    )
    registry = ToolRegistry()
    publish_catalog(manager, registry)
    sink = CollectingEventSink()
    activity_writer = ActivityWriter()

    def reject_render(_: CallToolResult) -> tuple[str, bool]:
        raise AssertionError("unsafe content reached MCP rendering")

    monkeypatch.setattr(mcp_adapter, "_bounded_content", reject_render)

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="unsafe-text",
            tool_name="mcp.fixture.echo",
            arguments={"text": "hello"},
        ),
        context=context(
            tmp_path,
            sink=sink,
            activity_writer=activity_writer,
        ),
    )

    assert result.error is not None
    assert result.error.code is ToolErrorCode.EXECUTION_FAILED
    assert result.error.retryable is False
    assert result.error.message == (
        "MCP tool returned content outside safe resource limits."
    )
    assert "sensitive" not in result.model_dump_json()
    assert len(manager.calls) == 1
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_FAILED,
    ]
    assert len(activity_writer.activities) == 1
    assert activity_writer.activities[0].outcome == "error"
    assert activity_writer.activities[0].error_code == ToolErrorCode.EXECUTION_FAILED


def test_content_preflight_accepts_well_formed_supplementary_unicode() -> None:
    mcp_adapter._preflight_content(
        CallToolResult(content=[TextContent(type="text", text="emoji: \U0001f600")])
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary",
    ("wire_bytes", "array", "nodes", "depth", "cycle", "non_json"),
)
async def test_adapter_rejects_unsafe_structured_output_before_validation_or_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    structured_tool = Tool(
        name="echo",
        inputSchema={"type": "object"},
        outputSchema={},
    )
    manager = FakeManager()
    manager.replace_catalog(
        compile_mcp_catalog((structured_tool,), server_id="fixture", generation=6)
    )
    compiled = manager.catalog("fixture").compiled_tools[0]
    output_validator = compiled.output_validator
    assert output_validator is not None

    value: object
    if boundary == "wire_bytes":
        value = {"value": "sensitive" + ("x" * mcp_adapter._MAX_STRUCTURED_WIRE_BYTES)}
    elif boundary == "array":
        value = {"value": [None] * (mcp_adapter._MAX_STRUCTURED_WIRE_BYTES + 1)}
    elif boundary == "nodes":
        value = {"value": [None] * (mcp_adapter._MAX_STRUCTURED_NODES + 1)}
    elif boundary == "depth":
        value = None
        for _ in range(mcp_adapter._MAX_STRUCTURED_DEPTH + 1):
            value = [value]
        value = {"value": value}
    elif boundary == "cycle":
        cycle: list[object] = []
        cycle.append(cycle)
        value = {"value": cycle}
    else:
        value = {"value": object()}
    manager.result = CallToolResult.model_construct(
        content=[],
        structuredContent=value,
        isError=False,
    )

    output_validation_calls = 0
    validator_type = type(output_validator)
    original_validate = validator_type.validate

    def validate_without_unsafe_output(
        validator: Validator,
        instance: object,
    ) -> None:
        nonlocal output_validation_calls
        if validator is output_validator:
            output_validation_calls += 1
            raise AssertionError("unsafe output reached JSON Schema validation")
        original_validate(validator, cast(JsonValue, instance))

    render_calls = 0

    def reject_render(_: CallToolResult) -> tuple[str, bool]:
        nonlocal render_calls
        render_calls += 1
        raise AssertionError("unsafe output reached JSON rendering")

    monkeypatch.setattr(validator_type, "validate", validate_without_unsafe_output)
    monkeypatch.setattr(mcp_adapter, "_bounded_content", reject_render)
    registry = ToolRegistry()
    publish_catalog(manager, registry)

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id=f"unsafe-{boundary}",
            tool_name="mcp.fixture.echo",
            arguments={},
        ),
        context=context(tmp_path),
    )

    assert result.error is not None
    assert result.error.code is ToolErrorCode.EXECUTION_FAILED
    assert result.error.retryable is False
    assert (
        result.error.message
        == "MCP tool returned structured output outside safe resource limits."
    )
    assert "sensitive" not in result.model_dump_json()
    assert output_validation_calls == 0
    assert render_calls == 0


@pytest.mark.asyncio
async def test_adapter_rejects_stale_generation_without_external_call(
    tmp_path: Path,
) -> None:
    manager = FakeManager()
    registry = ToolRegistry()
    publish_catalog(manager, registry)
    manager.replace_catalog(
        compile_mcp_catalog((echo_tool(),), server_id="fixture", generation=2)
    )

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="stale",
            tool_name="mcp.fixture.echo",
            arguments={"text": "hello"},
        ),
        context=context(tmp_path),
    )

    assert result.error is not None
    assert result.error.code is ToolErrorCode.EXECUTION_FAILED
    assert manager.calls == []
