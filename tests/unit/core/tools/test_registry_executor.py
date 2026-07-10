import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from awesome_agent.core.events import CollectingEventSink, EventEmitter, EventType
from awesome_agent.core.tools import (
    ExpectedToolFailure,
    ToolErrorCode,
    ToolExecutionContext,
    ToolHandler,
    ToolOutput,
    ToolRequest,
    ToolSpec,
    ToolStatus,
)
from awesome_agent.core.tools.executor import ToolExecutor
from awesome_agent.core.tools.registry import DuplicateToolName, ToolRegistry
from awesome_agent.core.workspace import resolve_workspace


class EmptyArguments(BaseModel):
    pass


async def handler(arguments: BaseModel, context: object) -> ToolOutput:
    return ToolOutput(content="ok")


class EchoArguments(BaseModel):
    model_config = ConfigDict(strict=True)

    text: str


def execution_context(
    tmp_path: Path,
) -> tuple[ToolExecutionContext, CollectingEventSink]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sink = CollectingEventSink()
    context = ToolExecutionContext(
        workspace=resolve_workspace(workspace),
        operation_id="operation_1",
        turn_id="turn_1",
        emitter=EventEmitter(session_id="session_1", sink=sink),
    )
    return context, sink


def echo_registry(handler_override: ToolHandler | None = None) -> ToolRegistry:
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
            read_only=True,
        ),
        input_model=EchoArguments,
        handler=echo if handler_override is None else handler_override,
    )
    return registry


def test_registry_rejects_duplicates_and_lists_sorted_specs() -> None:
    registry = ToolRegistry()
    for name in ("grep", "ls"):
        registry.register(
            spec=ToolSpec(
                name=name,
                description=name,
                input_schema=EmptyArguments.model_json_schema(),
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


@pytest.mark.asyncio
async def test_executor_emits_success_events(tmp_path: Path) -> None:
    context, sink = execution_context(tmp_path)
    executor = ToolExecutor(echo_registry())

    result = await executor.execute(
        ToolRequest(call_id="call_1", tool_name="echo", arguments={"text": "ok"}),
        context=context,
    )

    assert result.status is ToolStatus.SUCCESS
    assert result.content == "ok"
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_RESULT,
    ]


@pytest.mark.asyncio
async def test_executor_normalizes_invalid_arguments(tmp_path: Path) -> None:
    context, sink = execution_context(tmp_path)
    executor = ToolExecutor(echo_registry())

    result = await executor.execute(
        ToolRequest(call_id="call_1", tool_name="echo", arguments={}),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_RESULT,
    ]


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

    context, sink = execution_context(tmp_path)
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
        EventType.TOOL_RESULT,
    ]


@pytest.mark.asyncio
async def test_executor_normalizes_timeout(tmp_path: Path) -> None:
    waiting = asyncio.Event()

    async def wait_forever(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        await waiting.wait()
        return ToolOutput(content="unreachable")

    context, sink = execution_context(tmp_path)
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
        EventType.TOOL_RESULT,
    ]


@pytest.mark.asyncio
async def test_executor_propagates_cancellation_without_result_event(
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

    context, sink = execution_context(tmp_path)
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
    assert [event.event_type for event in sink.events] == [EventType.TOOL_STARTED]
