from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import JsonValue

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
from awesome_agent.context import (
    CODING_AGENT_PRODUCT_INSTRUCTIONS,
    estimate_messages,
    model_identity_context_source,
)
from awesome_agent.modeling import (
    AssistantMessage,
    GatewayEvent,
    ModelErrorCode,
    ModelErrorInfo,
    ModelIdentitySnapshot,
    ModelRequest,
    ModelTurn,
    StopReason,
    TurnCompleted,
    TurnFailed,
    UserMessage,
)


class Gateway:
    def __init__(self, scripts: tuple[tuple[GatewayEvent, ...], ...]) -> None:
        self.scripts = list(scripts)
        self.requests: list[ModelRequest] = []

    async def stream(
        self, selected: object, request: ModelRequest
    ) -> AsyncIterator[GatewayEvent]:
        del selected
        self.requests.append(request)
        for event in self.scripts.pop(0):
            yield event


def test_product_instructions_define_minimal_action() -> None:
    instructions = CODING_AGENT_PRODUCT_INSTRUCTIONS
    assert instructions.startswith("You are Awesome")
    assert "smallest set of actions" in instructions
    assert "explicitly requested verification or testing" in instructions
    assert "Never invoke a tool as a ritual" in instructions


def test_runtime_identity_source_is_authoritative_and_concise() -> None:
    source = model_identity_context_source(
        ModelIdentitySnapshot.from_models(
            configured_model="deepseek/deepseek-v4-pro",
            effective_model="deepseek/deepseek-v4-flash",
        ),
    )

    assert "Runtime: Awesome Agent" in source.content
    assert "Effective model: deepseek/deepseek-v4-flash" in source.content
    assert "deepseek/deepseek-v4-pro" not in source.content
    assert "one concise sentence" in source.content
    assert "never claim to be Claude" in source.content
    for omitted in (
        "Provider:",
        "Configured model:",
        "Fallback active:",
        "Workspace:",
        "Credential:",
        "Permission:",
        "Memory:",
        "MCP:",
        "Thinking:",
    ):
        assert omitted not in source.content


class Compressor:
    def __init__(self, result: AgentCompressionResult) -> None:
        self.result = result
        self.calls = 0
        self.retry_limits: list[int] = []

    async def compress(
        self,
        state: object,
        *,
        max_provider_retries: int,
    ) -> AgentCompressionResult:
        del state
        self.calls += 1
        self.retry_limits.append(max_provider_retries)
        return self.result


class Projector:
    def __init__(self) -> None:
        self.context: list[bool] = []
        self.warnings: list[str] = []

    async def project_gateway(self, event: GatewayEvent) -> None:
        del event

    async def project_tool(self, result: object) -> None:
        del result

    async def project_context(
        self, *, source_count: int, estimated_tokens: int, compressed: bool
    ) -> None:
        del source_count, estimated_tokens
        self.context.append(compressed)

    async def project_warning(self, *, code: str, message: str) -> None:
        del message
        self.warnings.append(code)


def _completed(content: str) -> TurnCompleted:
    return TurnCompleted(
        turn=ModelTurn(
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            assistant=AssistantMessage(content=content),
            stop_reason=StopReason.COMPLETED,
        )
    )


def _prepared(*, recommended: bool, content: str = "before") -> PreparedAgentContext:
    return PreparedAgentContext(
        messages=(UserMessage(content=content),),
        manifest=({"kind": "current_input", "source_id": "input"},),
        estimated_input_tokens=90 if recommended else 20,
        effective_input_limit=100,
        compression_recommended=recommended,
    )


async def _invoke(
    gateway: Gateway,
    compressor: Compressor,
    prepared: PreparedAgentContext,
    projector: Projector,
    snapshot_records: list[tuple[dict[str, JsonValue], ...]] | None = None,
) -> AgentState:
    async def builder(state: AgentState) -> PreparedAgentContext:
        del state
        return prepared

    async def record_snapshot(manifest: tuple[dict[str, JsonValue], ...]) -> None:
        if snapshot_records is not None:
            snapshot_records.append(manifest)

    runtime = AgentRuntimeContext(
        gateway=cast(Any, gateway),
        executor=cast(Any, object()),
        tool_catalog=lambda: (),
        tool_context_factory=cast(Any, lambda state: None),
        event_projector=cast(Any, projector),
        context_builder=builder,
        compressor=compressor,
        budget=TurnBudget(),
        monotonic=lambda: 1.0,
        context_token_estimator=estimate_messages,
        context_snapshot_recorder=record_snapshot,
    )
    graph = compile_agent_graph(InMemorySaver())
    return validate_agent_state(
        await graph.ainvoke(
            new_agent_state(
                thread_id="thread_1",
                turn_id="turn_1",
                workspace_key="workspace_1",
                provider="deepseek",
                model="deepseek/deepseek-v4-flash",
                thinking_enabled=False,
            ),
            config={"configurable": {"thread_id": "turn_1"}},
            context=runtime,
        )
    )


@pytest.mark.asyncio
async def test_automatic_compression_runs_at_threshold_not_below() -> None:
    compressed = _prepared(recommended=False, content="compressed")
    compressor = Compressor(
        AgentCompressionResult(completed=True, attempted=True, prepared=compressed)
    )
    projector = Projector()
    snapshot_records: list[tuple[dict[str, JsonValue], ...]] = []
    initial = _prepared(recommended=True)

    result = await _invoke(
        Gateway(((_completed("done"),),)),
        compressor,
        initial,
        projector,
        snapshot_records,
    )

    assert compressor.calls == 1
    assert result["compressions"] == 1
    assert result["messages"][0]["content"] == "compressed"
    assert projector.context == [False, True]
    assert snapshot_records == [initial.manifest, compressed.manifest]

    below = Compressor(AgentCompressionResult(completed=False, attempted=False))
    await _invoke(
        Gateway(((_completed("done"),),)),
        below,
        _prepared(recommended=False),
        Projector(),
    )
    assert below.calls == 0


@pytest.mark.asyncio
async def test_context_length_error_routes_once_through_compression() -> None:
    failure = TurnFailed(
        error=ModelErrorInfo(
            code=ModelErrorCode.CONTEXT_LENGTH,
            message="too long",
            retryable=False,
            provider="deepseek",
        )
    )
    compressor = Compressor(
        AgentCompressionResult(
            completed=True,
            attempted=True,
            prepared=_prepared(recommended=False, content="smaller"),
        )
    )

    result = await _invoke(
        Gateway(((failure,), (_completed("done"),))),
        compressor,
        _prepared(recommended=False),
        Projector(),
    )

    assert compressor.calls == 1
    assert result["final_answer"] == "done"
    assert result["model_calls"] == 3


@pytest.mark.asyncio
async def test_compression_failure_is_warning_when_context_fits() -> None:
    compressor = Compressor(
        AgentCompressionResult(
            completed=False,
            attempted=True,
            error_code="compression_failed",
        )
    )
    projector = Projector()

    result = await _invoke(
        Gateway(((_completed("done"),),)),
        compressor,
        _prepared(recommended=True),
        projector,
    )

    assert result["final_answer"] == "done"
    assert projector.warnings == ["compression_failed"]


@pytest.mark.asyncio
async def test_unrecoverable_compression_never_retries_without_the_tool_tail() -> None:
    compressor = Compressor(
        AgentCompressionResult(
            completed=False,
            attempted=True,
            error_code="context_unrecoverable",
        )
    )
    projector = Projector()

    result = await _invoke(
        Gateway(()),
        compressor,
        _prepared(recommended=True),
        projector,
    )

    assert compressor.calls == 1
    assert result["termination_reason"] == "context_unrecoverable"
    assert projector.warnings == []


@pytest.mark.asyncio
async def test_automatic_compression_cannot_consume_reserved_final_model_call() -> None:
    compressor = Compressor(
        AgentCompressionResult(
            completed=True,
            attempted=True,
            prepared=_prepared(recommended=False),
        )
    )
    projector = Projector()

    async def builder(state: object) -> PreparedAgentContext:
        del state
        return _prepared(recommended=True)

    runtime = AgentRuntimeContext(
        gateway=cast(Any, Gateway(((_completed("done"),),))),
        executor=cast(Any, object()),
        tool_catalog=lambda: (),
        tool_context_factory=cast(Any, lambda state: None),
        event_projector=cast(Any, projector),
        context_builder=builder,
        compressor=compressor,
        budget=TurnBudget(model_calls=1),
        monotonic=lambda: 1.0,
        context_token_estimator=estimate_messages,
    )
    result = await compile_agent_graph(InMemorySaver()).ainvoke(
        new_agent_state(
            thread_id="thread_1",
            turn_id="turn_1",
            workspace_key="workspace_1",
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            thinking_enabled=False,
        ),
        config={"configurable": {"thread_id": "turn_1"}},
        context=runtime,
    )

    assert compressor.calls == 0
    assert result["final_answer"] == "done"
    assert projector.warnings == ["model_budget_reserved"]
