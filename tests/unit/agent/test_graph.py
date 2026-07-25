from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError, NodeCancelledError

from awesome_agent.agent import (
    AgentCompressionResult,
    AgentRuntimeContext,
    AgentState,
    PreparedAgentContext,
    TurnBudget,
    compile_agent_graph,
    new_agent_state,
    validate_agent_state,
)
from awesome_agent.agent.context import DisabledAgentContextCompressor
from awesome_agent.context import estimate_messages
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
    ProviderRetrying,
    StopReason,
    ToolCall,
    ToolResultMessage,
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

    async def project_context(
        self,
        *,
        source_count: int,
        estimated_tokens: int,
        compressed: bool,
    ) -> None:
        del source_count, estimated_tokens, compressed

    async def project_warning(self, *, code: str, message: str) -> None:
        del code, message

    async def project_memory_status(self, *, enabled: bool, status: str) -> None:
        del enabled, status


class SuccessfulCompressor:
    def __init__(self, *, provider_retries: int = 0) -> None:
        self.states: list[AgentState] = []
        self.retry_limits: list[int] = []
        self.provider_retries = provider_retries

    async def compress(
        self,
        state: AgentState,
        *,
        max_provider_retries: int,
    ) -> AgentCompressionResult:
        self.states.append(state)
        self.retry_limits.append(max_provider_retries)
        assert self.provider_retries <= max_provider_retries
        tail = tuple(
            (
                AssistantMessage.model_validate(message)
                if message.get("role") == "assistant"
                else ToolResultMessage.model_validate(message)
            )
            for message in state["messages"][len(state["context_manifest"]) :]
        )
        return AgentCompressionResult(
            completed=True,
            attempted=True,
            prepared=PreparedAgentContext(
                messages=(UserMessage(content="compressed base"), *tail),
                manifest=({"kind": "current_input", "source_id": "compressed"},),
                estimated_input_tokens=4 + estimate_messages(tail),
                effective_input_limit=1_000,
            ),
            usage=ModelUsage(provider_retries=self.provider_retries),
        )


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
    compressor: SuccessfulCompressor | None = None,
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
                capability="workspace.read",
                read_only=True,
            ),
            ToolSpec(
                name="edit_file",
                description="Edit a file",
                input_schema={"type": "object"},
                capability="workspace.write",
                read_only=False,
            ),
        ),
        tool_context_factory=lambda state, request: cast(
            Any,
            {"turn_id": state["turn_id"], "tool_name": request.tool_name},
        ),
        event_projector=FakeProjector(),
        context_builder=context_builder,
        budget=budget or TurnBudget(),
        monotonic=_Monotonic(),
        context_token_estimator=estimate_messages,
        compressor=compressor or DisabledAgentContextCompressor(),
    )


class _Monotonic:
    def __init__(self) -> None:
        self._value = 0.0

    def __call__(self) -> float:
        self._value += 0.01
        return self._value


def _state() -> AgentState:
    return new_agent_state(
        thread_id="thread_1",
        turn_id="turn_1",
        workspace_key="workspace_1",
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        thinking_enabled=False,
    )


async def _invoke(
    runtime: AgentRuntimeContext,
    *,
    state: AgentState | None = None,
    recursion_limit: int = 2_048,
) -> AgentState:
    graph = compile_agent_graph(InMemorySaver())
    config: RunnableConfig = {
        "configurable": {"thread_id": "turn_1"},
        "recursion_limit": recursion_limit,
    }
    return validate_agent_state(
        await graph.ainvoke(
            state or _state(),
            config=config,
            context=runtime,
        )
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
async def test_duplicate_tool_call_ids_are_rejected_before_any_tool_executes() -> None:
    calls = (
        ToolCall(call_id="call_duplicate", name="read_file", arguments_json="{}"),
        ToolCall(call_id="call_duplicate", name="edit_file", arguments_json="{}"),
    )
    gateway = FakeGateway(((_completed("", tool_calls=calls),),))
    executor = FakeExecutor()

    result = await _invoke(_runtime(gateway, executor))

    assert executor.requests == []
    assert result["pending_tool_calls"] == []
    assert result["tool_results"] == []
    assert result["termination_reason"] == "model_provider_protocol"


@pytest.mark.asyncio
async def test_reused_tool_call_id_is_rejected_before_the_new_handler_runs() -> None:
    first = ToolCall(call_id="call_reused", name="read_file", arguments_json="{}")
    reused = ToolCall(call_id="call_reused", name="edit_file", arguments_json="{}")
    gateway = FakeGateway(
        (
            (_completed("", tool_calls=(first,)),),
            (_completed("", tool_calls=(reused,)),),
        )
    )
    executor = FakeExecutor()

    result = await _invoke(_runtime(gateway, executor))

    assert [request.tool_name for request in executor.requests] == ["read_file"]
    assert [item["call_id"] for item in result["tool_results"]] == ["call_reused"]
    assert result["pending_tool_calls"] == []
    assert result["termination_reason"] == "model_provider_protocol"
    assert result["tool_calls"] == 1
    assert len(gateway.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    (
        ToolCall(call_id="x" * 129, name="read_file", arguments_json="{}"),
        ToolCall(call_id="call_invalid_name", name="foo.bar", arguments_json="{}"),
    ),
)
async def test_undispatchable_tool_call_is_rejected_before_execution(
    call: ToolCall,
) -> None:
    gateway = FakeGateway(((_completed("", tool_calls=(call,)),),))
    executor = FakeExecutor()

    result = await _invoke(_runtime(gateway, executor))

    assert executor.requests == []
    assert result["pending_tool_calls"] == []
    assert result["tool_results"] == []
    assert result["termination_reason"] == "model_provider_protocol"


@pytest.mark.asyncio
async def test_tool_call_at_execution_contract_id_limit_remains_valid() -> None:
    call = ToolCall(call_id="x" * 128, name="read_file", arguments_json="{}")
    gateway = FakeGateway(
        ((_completed("", tool_calls=(call,)),), (_completed("done"),))
    )
    executor = FakeExecutor()

    result = await _invoke(_runtime(gateway, executor))

    assert [request.call_id for request in executor.requests] == [call.call_id]
    assert result["final_answer"] == "done"
    assert result["termination_reason"] == "completed"


@pytest.mark.asyncio
async def test_create_only_executes_the_requested_write_then_stops() -> None:
    write = ToolCall(
        call_id="call_write",
        name="write_file",
        arguments_json='{"path":"circle_area.py","content":"pass\\n"}',
    )
    gateway = FakeGateway(
        ((_completed("", tool_calls=(write,)),), (_completed("Created the file."),))
    )
    executor = FakeExecutor()

    result = await _invoke(_runtime(gateway, executor))

    assert [request.tool_name for request in executor.requests] == ["write_file"]
    assert len(gateway.requests) == 2
    assert result["final_answer"] == "Created the file."


@pytest.mark.asyncio
async def test_tool_failure_is_observed_before_one_corrected_tool_call() -> None:
    first = ToolCall(
        call_id="call_read",
        name="read_file",
        arguments_json="{}",
    )
    corrected = ToolCall(
        call_id="call_edit",
        name="edit_file",
        arguments_json="{}",
    )
    failure = ToolResult(
        call_id="call_read",
        tool_name="read_file",
        status=ToolStatus.ERROR,
        content="not found",
        error=ToolError(code=ToolErrorCode.NOT_FOUND, message="not found"),
    )
    success = ToolResult(
        call_id="call_edit",
        tool_name="edit_file",
        status=ToolStatus.SUCCESS,
        content="edited",
    )
    gateway = FakeGateway(
        (
            (_completed("", tool_calls=(first,)),),
            (_completed("", tool_calls=(corrected,)),),
            (_completed("done"),),
        )
    )

    executor = FakeExecutor((failure, success))
    await _invoke(_runtime(gateway, executor))

    assert [request.call_id for request in executor.requests] == [
        "call_read",
        "call_edit",
    ]
    first_observation = gateway.requests[1].messages[-1]
    corrected_observation = gateway.requests[2].messages[-1]
    assert first_observation.role == "tool"
    assert corrected_observation.role == "tool"
    assert first_observation.call_id == "call_read"
    assert corrected_observation.call_id == "call_edit"


@pytest.mark.asyncio
async def test_context_compression_preserves_executed_tool_tail_without_replay() -> (
    None
):
    first = ToolCall(
        call_id="call_a",
        name="read_file",
        arguments_json='{"path":"a.txt"}',
    )
    second = ToolCall(
        call_id="call_b",
        name="edit_file",
        arguments_json='{"path":"b.txt"}',
    )
    context_length = TurnFailed(
        error=ModelErrorInfo(
            code=ModelErrorCode.CONTEXT_LENGTH,
            message="too long",
            retryable=False,
            provider="deepseek",
        )
    )
    gateway = FakeGateway(
        (
            (_completed("", tool_calls=(first,)),),
            (context_length,),
            (_completed("", tool_calls=(second,)),),
            (_completed("done"),),
        )
    )
    executor = FakeExecutor()
    compressor = SuccessfulCompressor()

    result = await _invoke(
        _runtime(gateway, executor, compressor=compressor),
    )

    assert [request.call_id for request in executor.requests] == ["call_a", "call_b"]
    retried_messages = gateway.requests[2].messages
    assert [message.role for message in retried_messages[-2:]] == [
        "assistant",
        "tool",
    ]
    assert cast(AssistantMessage, retried_messages[-2]).tool_calls == (first,)
    assert cast(ToolResultMessage, retried_messages[-1]).call_id == "call_a"
    assert result["tool_calls"] == 2
    assert [item["call_id"] for item in result["tool_results"]] == [
        "call_a",
        "call_b",
    ]


@pytest.mark.asyncio
async def test_compression_retries_preserve_the_reserved_final_model_attempt() -> None:
    compressor = SuccessfulCompressor(provider_retries=1)
    gateway = FakeGateway(((_completed("reserved final"),),))
    state = _state()
    current = UserMessage(content="large frozen context")
    state["messages"] = [current.model_dump(mode="json")]
    state["context_manifest"] = [
        {"kind": "current_input", "source_id": "input", "order": 0}
    ]
    state["context_estimated_tokens"] = estimate_messages((current,))
    state["context_effective_limit"] = 1_000
    state["compression_requested"] = True
    state["compression_reason"] = "automatic"

    result = await _invoke(
        _runtime(
            gateway,
            budget=TurnBudget(model_calls=3, provider_retries=2),
            compressor=compressor,
        ),
        state=state,
    )

    assert compressor.retry_limits == [1]
    assert len(gateway.requests) == 1
    assert gateway.requests[0].tools == ()
    assert result["final_answer"] == "reserved final"
    assert result["model_calls"] == 3
    assert result["provider_retries"] == 1


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
async def test_model_failure_after_tool_batch_closes_pending_without_retry() -> None:
    call = ToolCall(call_id="call_1", name="read_file", arguments_json="{}")
    failure = TurnFailed(
        error=ModelErrorInfo(
            code=ModelErrorCode.AUTHENTICATION,
            message="safe failure",
            retryable=False,
            provider="deepseek",
        )
    )
    gateway = FakeGateway(((_completed("", tool_calls=(call,)),), (failure,)))
    executor = FakeExecutor()

    result = await _invoke(_runtime(gateway, executor))

    assert len(gateway.requests) == 2
    assert [request.call_id for request in executor.requests] == ["call_1"]
    assert result["pending_tool_calls"] == []
    assert result["next_tool_index"] == 0
    assert result["termination_reason"] == "model_authentication"


@pytest.mark.asyncio
async def test_invalid_tool_arguments_consume_tool_budget_without_executor_call() -> (
    None
):
    call = ToolCall(
        call_id="call_invalid",
        name="read_file",
        arguments_json="[]",
    )
    gateway = FakeGateway(
        ((_completed("", tool_calls=(call,)),), (_completed("recovered"),))
    )
    executor = FakeExecutor()

    result = await _invoke(_runtime(gateway, executor))

    assert result["tool_calls"] == 1
    assert executor.requests == []
    observation = gateway.requests[1].messages[-1]
    assert observation.role == "tool"
    assert observation.is_error is True


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
    assert result["termination_reason"] == (
        "context_unrecoverable"
        if code is ModelErrorCode.CONTEXT_LENGTH
        else f"model_{code.value}"
    )
    assert result["model_calls"] == 1


@pytest.mark.asyncio
async def test_failed_retry_attempts_are_charged() -> None:
    failure = TurnFailed(
        error=ModelErrorInfo(
            code=ModelErrorCode.AUTHENTICATION,
            message="safe failure",
            retryable=False,
            provider="deepseek",
        )
    )
    gateway = FakeGateway(
        (
            (
                ProviderRetrying(
                    attempt=2,
                    maximum=3,
                    delay_seconds=0.1,
                    error_code=ModelErrorCode.TRANSIENT,
                ),
                failure,
            ),
        )
    )

    result = await _invoke(_runtime(gateway))

    assert result["model_calls"] == 2
    assert result["provider_retries"] == 1


@pytest.mark.asyncio
async def test_retry_is_stopped_before_exceeding_turn_budget() -> None:
    retrying = ProviderRetrying(
        attempt=2,
        maximum=3,
        delay_seconds=0.1,
        error_code=ModelErrorCode.TRANSIENT,
    )
    gateway = FakeGateway(((retrying, retrying),))

    result = await _invoke(_runtime(gateway, budget=TurnBudget(provider_retries=1)))

    assert result["model_calls"] == 2
    assert result["provider_retries"] == 1
    assert result["termination_reason"] == "provider_retry_budget_exhausted"


@pytest.mark.asyncio
async def test_zero_retry_and_compression_budgets_still_allow_normal_calls() -> None:
    gateway = FakeGateway(((_completed("done"),),))

    result = await _invoke(
        _runtime(
            gateway,
            budget=TurnBudget(provider_retries=0, compressions=0),
        )
    )

    assert result["final_answer"] == "done"
    assert result["termination_reason"] == "completed"


@pytest.mark.asyncio
async def test_graph_does_not_mutate_caller_owned_state_lists() -> None:
    initial = _state()
    original_messages = list(cast(list[object], initial["messages"]))

    await _invoke(_runtime(FakeGateway(((_completed("done"),),))), state=initial)

    assert initial["messages"] == original_messages
    assert initial["tool_results"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("budget", "termination_reason"),
    (
        (TurnBudget(tool_calls=1), "tool_budget_exhausted"),
        (
            TurnBudget(active_execution_seconds=0.015),
            "active_time_budget_exhausted",
        ),
    ),
)
async def test_loop_budget_exhaustion_closes_tool_batch_before_reserved_final_call(
    budget: TurnBudget,
    termination_reason: str,
) -> None:
    calls = (
        ToolCall(call_id="call_1", name="read_file", arguments_json="{}"),
        ToolCall(call_id="call_2", name="read_file", arguments_json="{}"),
    )
    gateway = FakeGateway(
        ((_completed("partial", tool_calls=calls),), (_completed("summary"),))
    )
    executor = FakeExecutor()

    result = await _invoke(
        _runtime(gateway, executor, budget=budget),
    )

    assert [request.call_id for request in executor.requests] == ["call_1"]
    assert gateway.requests[1].tools == ()
    closing_messages = gateway.requests[1].messages[-3:]
    assert [message.role for message in closing_messages] == [
        "assistant",
        "tool",
        "tool",
    ]
    assert cast(ToolResultMessage, closing_messages[1]).call_id == "call_1"
    skipped = cast(ToolResultMessage, closing_messages[2])
    assert skipped.call_id == "call_2"
    assert skipped.is_error is True
    assert termination_reason in skipped.content
    assert [item["call_id"] for item in result["tool_results"]] == [
        "call_1",
        "call_2",
    ]
    assert result["final_answer"] == "summary"
    assert result["termination_reason"] == termination_reason


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
