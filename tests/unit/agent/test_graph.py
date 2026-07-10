from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError, NodeCancelledError

from awesome_agent.agent import (
    AgentRuntimeContext,
    PreparedAgentContext,
    TurnBudget,
    compile_agent_graph,
    new_agent_state,
)
from awesome_agent.core.tools import (
    ToolError,
    ToolErrorCode,
    ToolRequest,
    ToolResult,
    ToolSpec,
    ToolStatus,
)
from awesome_agent.modeling import (
    AssistantMessage,
    GatewayEvent,
    ModelErrorCode,
    ModelErrorInfo,
    ModelRequest,
    ModelTurn,
    ModelUsage,
    StopReason,
    ToolCall,
    TurnCompleted,
    TurnFailed,
    UserMessage,
)


class FakeGateway:
    def __init__(self, scripts: tuple[tuple[GatewayEvent, ...], ...]) -> None:
        self._scripts = list(scripts)
        self.requests: list[ModelRequest] = []

    async def stream(
        self,
        selected: object,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]:
        del selected
        self.requests.append(request)
        for event in self._scripts.pop(0):
            yield event


class FakeExecutor:
    def __init__(
        self,
        results: tuple[ToolResult, ...] = (),
        *,
        cancel: bool = False,
    ) -> None:
        self._results = list(results)
        self._cancel = cancel
        self.requests: list[ToolRequest] = []

    async def execute(self, request: ToolRequest, *, context: object) -> ToolResult:
        del context
        self.requests.append(request)
        if self._cancel:
            raise asyncio.CancelledError
        if self._results:
            return self._results.pop(0)
        return ToolResult(
            call_id=request.call_id,
            tool_name=request.tool_name,
            status=ToolStatus.SUCCESS,
            content=f"result:{request.tool_name}",
        )


class FakeProjector:
    def __init__(self) -> None:
        self.gateway_events: list[GatewayEvent] = []
        self.tool_results: list[ToolResult] = []

    async def project_gateway(self, event: GatewayEvent) -> None:
        self.gateway_events.append(event)

    async def project_tool(self, result: ToolResult) -> None:
        self.tool_results.append(result)


def _completed(
    content: str,
    *,
    tool_calls: tuple[ToolCall, ...] = (),
    retries: int = 0,
) -> TurnCompleted:
    return TurnCompleted(
        turn=ModelTurn(
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            assistant=AssistantMessage(content=content, tool_calls=tool_calls),
            stop_reason=(StopReason.TOOL_CALLS if tool_calls else StopReason.COMPLETED),
            usage=ModelUsage(provider_retries=retries),
        )
    )


def _runtime(
    gateway: FakeGateway,
    executor: FakeExecutor | None = None,
    *,
    budget: TurnBudget | None = None,
) -> AgentRuntimeContext:
    async def context_builder(state: object) -> PreparedAgentContext:
        del state
        return PreparedAgentContext(
            messages=(UserMessage(content="inspect"),),
            manifest=({"kind": "temporary_thread_history", "count": 1},),
        )

    return AgentRuntimeContext(
        gateway=cast(Any, gateway),
        executor=cast(Any, executor or FakeExecutor()),
        tool_catalog=lambda: (
            ToolSpec(
                name="read_file",
                description="Read a file",
                input_schema={"type": "object"},
                read_only=True,
            ),
            ToolSpec(
                name="edit_file",
                description="Edit a file",
                input_schema={"type": "object"},
                read_only=False,
            ),
        ),
        tool_context_factory=lambda state: cast(Any, {"turn_id": state["turn_id"]}),
        event_projector=FakeProjector(),
        context_builder=context_builder,
        budget=budget or TurnBudget(),
        monotonic=_Monotonic(),
    )


class _Monotonic:
    def __init__(self) -> None:
        self._value = 0.0

    def __call__(self) -> float:
        self._value += 0.01
        return self._value


def _state() -> dict[str, object]:
    return dict(
        new_agent_state(
            thread_id="thread_1",
            turn_id="turn_1",
            workspace_key="workspace_1",
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            thinking_enabled=False,
        )
    )


async def _invoke(
    runtime: AgentRuntimeContext,
    *,
    state: dict[str, object] | None = None,
    recursion_limit: int = 2_048,
) -> dict[str, object]:
    graph = compile_agent_graph(InMemorySaver())
    return await graph.ainvoke(
        state or _state(),
        config={
            "configurable": {"thread_id": "turn_1"},
            "recursion_limit": recursion_limit,
        },
        context=runtime,
    )


@pytest.mark.asyncio
async def test_final_text_completes_without_tools() -> None:
    gateway = FakeGateway(((_completed("done"),),))

    result = await _invoke(_runtime(gateway))

    assert result["final_answer"] == "done"
    assert result["termination_reason"] == "completed"
    assert result["model_calls"] == 1


@pytest.mark.asyncio
async def test_three_tools_execute_in_provider_order_one_per_node() -> None:
    calls = tuple(
        ToolCall(call_id=f"call_{index}", name="read_file", arguments_json="{}")
        for index in range(3)
    )
    gateway = FakeGateway(((_completed("", tool_calls=calls),), (_completed("done"),)))
    executor = FakeExecutor()

    result = await _invoke(_runtime(gateway, executor))

    assert [request.call_id for request in executor.requests] == [
        "call_0",
        "call_1",
        "call_2",
    ]
    assert result["tool_calls"] == 3
    assert len(gateway.requests) == 2
    tool_messages = [
        message for message in gateway.requests[1].messages if message.role == "tool"
    ]
    assert [message.call_id for message in tool_messages] == [
        "call_0",
        "call_1",
        "call_2",
    ]


@pytest.mark.asyncio
async def test_normalized_tool_error_returns_to_model_observation() -> None:
    call = ToolCall(call_id="call_1", name="read_file", arguments_json="{}")
    error = ToolResult(
        call_id="call_1",
        tool_name="read_file",
        status=ToolStatus.ERROR,
        content="not found",
        error=ToolError(code=ToolErrorCode.NOT_FOUND, message="not found"),
    )
    gateway = FakeGateway(
        ((_completed("", tool_calls=(call,)),), (_completed("recovered"),))
    )

    await _invoke(_runtime(gateway, FakeExecutor((error,))))

    observation = gateway.requests[1].messages[-1]
    assert observation.role == "tool"
    assert observation.is_error is True
    assert observation.content == "not found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [ModelErrorCode.CONTEXT_LENGTH, ModelErrorCode.AUTHENTICATION],
)
async def test_provider_failure_finalizes_with_best_visible_answer(
    code: ModelErrorCode,
) -> None:
    failure = TurnFailed(
        error=ModelErrorInfo(
            code=code,
            message="safe failure",
            retryable=False,
            provider="deepseek",
        )
    )
    gateway = FakeGateway(((failure,),))

    result = await _invoke(_runtime(gateway))

    assert result["final_answer"] is None
    assert result["termination_reason"] == f"model_{code.value}"


@pytest.mark.asyncio
async def test_budget_exhaustion_skips_tools_and_uses_reserved_final_call() -> None:
    calls = (
        ToolCall(call_id="call_1", name="read_file", arguments_json="{}"),
        ToolCall(call_id="call_2", name="read_file", arguments_json="{}"),
    )
    gateway = FakeGateway(
        ((_completed("partial", tool_calls=calls),), (_completed("summary"),))
    )
    executor = FakeExecutor()

    result = await _invoke(
        _runtime(gateway, executor, budget=TurnBudget(tool_calls=1)),
    )

    assert [request.call_id for request in executor.requests] == ["call_1"]
    assert gateway.requests[1].tools == ()
    assert result["final_answer"] == "summary"
    assert result["termination_reason"] == "tool_budget_exhausted"


@pytest.mark.asyncio
async def test_tool_cancellation_propagates() -> None:
    call = ToolCall(call_id="call_1", name="read_file", arguments_json="{}")
    gateway = FakeGateway(((_completed("", tool_calls=(call,)),),))

    with pytest.raises(NodeCancelledError):
        await _invoke(_runtime(gateway, FakeExecutor(cancel=True)))


@pytest.mark.asyncio
async def test_langgraph_recursion_limit_is_only_a_fail_safe() -> None:
    call = ToolCall(call_id="call_1", name="read_file", arguments_json="{}")
    gateway = FakeGateway(((_completed("", tool_calls=(call,)),),))

    with pytest.raises(GraphRecursionError):
        await _invoke(_runtime(gateway), recursion_limit=2)
