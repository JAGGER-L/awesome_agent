from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

from awesome_agent.core.events import CollectingEventSink, EventEmitter
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
from awesome_agent.extensions.mcp import McpCallUncertain
from awesome_agent.extensions.mcp.adapter import McpToolAdapter


class ActivityWriter(ToolActivityWriter):
    def finalize(self, activity: ToolActivityDraft) -> None:
        pass


class FakeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.result = CallToolResult(
            content=[TextContent(type="text", text="hello")],
        )
        self.uncertain = False

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> CallToolResult:
        self.calls.append((server_id, tool_name, arguments))
        if self.uncertain:
            raise McpCallUncertain("uncertain secret")
        return self.result


def context(tmp_path: Path) -> ToolExecutionContext:
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
            sink=CollectingEventSink(),
        ),
        activity_writer=ActivityWriter(),
        monotonic=lambda: next(ticks),
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
    McpToolAdapter(manager, "fixture").replace_registry_tools(
        registry,
        (echo_tool(),),
    )
    registered = registry.resolve("mcp.fixture.echo")
    assert registered is not None
    assert registered.spec.read_only is False

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
    McpToolAdapter(manager, "fixture").replace_registry_tools(
        registry,
        (echo_tool(),),
    )
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
async def test_adapter_never_replays_an_uncertain_external_call(tmp_path: Path) -> None:
    manager = FakeManager()
    manager.uncertain = True
    registry = ToolRegistry()
    McpToolAdapter(manager, "fixture").replace_registry_tools(
        registry,
        (echo_tool(),),
    )

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
