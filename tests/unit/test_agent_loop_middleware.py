from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from awesome_agent.modeling import (
    AssistantMessage,
    ContinuationState,
    ModelTurn,
    ModelUsage,
    StopReason,
    SystemMessage,
    ToolCall,
    ToolResultMessage,
)
from awesome_agent.observability.facade import ObservabilityFacade
from awesome_agent.observability.repository import InMemoryObservabilityRepository
from awesome_agent.runtime.agent_loop import (
    AgentLoopStatus,
    MiddlewareContext,
    MiddlewareDecision,
    MiddlewareStack,
    MiddlewareStage,
)
from awesome_agent.runtime.agent_loop.observability_middleware import (
    ObservabilityMiddleware,
)


class RecordingMiddleware:
    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        stop: bool = False,
    ) -> None:
        self.name = name
        self.events = events
        self.stop = stop

    async def handle(
        self,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[MiddlewareDecision]],
    ) -> MiddlewareDecision:
        self.events.append(f"{self.name}:enter:{stage.value}")
        if self.stop:
            self.events.append(f"{self.name}:stop:{context.runtime_route}")
            return MiddlewareDecision.stop(AgentLoopStatus.FAILED, "stopped")
        decision = await call_next(context)
        self.events.append(f"{self.name}:exit:{stage.value}")
        return decision


class WrappingMiddleware(RecordingMiddleware):
    async def wrap_stage(
        self,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        self.events.append(f"{self.name}:wrap-enter:{stage.value}")
        result = await call_next(context)
        self.events.append(f"{self.name}:wrap-exit:{stage.value}")
        return result


@pytest.mark.asyncio
async def test_middleware_stack_runs_in_registration_order() -> None:
    events: list[str] = []
    stack = MiddlewareStack(
        [
            RecordingMiddleware("first", events),
            RecordingMiddleware("second", events),
        ]
    )

    decision = await stack.run_stage(
        MiddlewareStage.BEFORE_MODEL,
        MiddlewareContext(
            run_id="run",
            agent_id="agent",
            runtime_route="solo-readonly",
            messages=[SystemMessage(content="hello")],
        ),
    )

    assert decision.continue_loop
    assert events == [
        "first:enter:before_model",
        "second:enter:before_model",
        "second:exit:before_model",
        "first:exit:before_model",
    ]


@pytest.mark.asyncio
async def test_middleware_stack_can_short_circuit_stage() -> None:
    events: list[str] = []
    stack = MiddlewareStack(
        [
            RecordingMiddleware("first", events),
            RecordingMiddleware("second", events, stop=True),
            RecordingMiddleware("third", events),
        ]
    )

    decision = await stack.run_stage(
        MiddlewareStage.WRAP_MODEL_CALL,
        MiddlewareContext(
            run_id="run",
            agent_id="agent",
            runtime_route="solo-readonly",
            messages=[SystemMessage(content="hello")],
        ),
    )

    assert not decision.continue_loop
    assert decision.status is AgentLoopStatus.FAILED
    assert decision.reason == "stopped"
    assert events == [
        "first:enter:wrap_model_call",
        "second:enter:wrap_model_call",
        "second:stop:solo-readonly",
        "first:exit:wrap_model_call",
    ]


@pytest.mark.asyncio
async def test_middleware_stack_wraps_operation_in_registration_order() -> None:
    events: list[str] = []
    stack = MiddlewareStack(
        [
            WrappingMiddleware("first", events),
            WrappingMiddleware("second", events),
        ]
    )

    async def operation() -> dict[str, Any]:
        events.append("operation")
        return {"handled": True}

    result = await stack.run_operation(
        MiddlewareStage.WRAP_MODEL_CALL,
        MiddlewareContext(
            run_id=str(uuid4()),
            agent_id=str(uuid4()),
            runtime_route="solo-readonly",
            messages=[SystemMessage(content="prompt text must stay private")],
        ),
        operation,
    )

    assert result == {"handled": True}
    assert events == [
        "first:wrap-enter:wrap_model_call",
        "second:wrap-enter:wrap_model_call",
        "operation",
        "second:wrap-exit:wrap_model_call",
        "first:wrap-exit:wrap_model_call",
    ]


@pytest.mark.asyncio
async def test_observability_middleware_records_safe_model_call_span() -> None:
    repository = InMemoryObservabilityRepository()
    exporter = RecordingExporter()
    stack = MiddlewareStack([ObservabilityMiddleware(_facade(repository, exporter))])
    run_id = uuid4()
    agent_id = uuid4()

    async def operation() -> dict[str, Any]:
        turn = ModelTurn(
            assistant=AssistantMessage(content="answer"),
            stop_reason=StopReason.COMPLETED,
            provider="deepseek",
            model="deepseek-v4-flash",
            usage=ModelUsage(input_tokens=10, output_tokens=20),
            continuation=ContinuationState(
                provider="deepseek",
                kind="responses",
                data={"private": "continuation-secret"},
            ),
        )
        return {
            "model_turn_count": 1,
            "last_turn": turn.model_dump(mode="json"),
            "continuation": {"private": "continuation-secret"},
            "messages": [
                SystemMessage(content="prompt text must stay private").model_dump(
                    mode="json"
                )
            ],
        }

    result = await stack.run_operation(
        MiddlewareStage.WRAP_MODEL_CALL,
        MiddlewareContext(
            run_id=str(run_id),
            agent_id=str(agent_id),
            runtime_route="solo-readonly",
            messages=[SystemMessage(content="prompt text must stay private")],
            metadata={
                "headers": "authorization-secret",
                "team_root_run_id": "team-root",
            },
        ),
        operation,
    )

    durable_spans = await repository.list_spans_for_run(run_id)
    assert result["model_turn_count"] == 1
    assert [span.name for span in durable_spans] == ["model.call"]
    assert durable_spans[0].attributes["runtime_route"] == "solo-readonly"
    assert durable_spans[0].attributes["agent_id"] == str(agent_id)
    assert durable_spans[0].attributes["provider"] == "deepseek"
    assert durable_spans[0].attributes["model"] == "deepseek-v4-flash"
    assert durable_spans[0].attributes["team_root_run_id"] == "team-root"
    assert "headers" not in durable_spans[0].attributes
    assert "messages" not in durable_spans[0].attributes
    assert "continuation" not in durable_spans[0].attributes
    assert "prompt text" not in str(durable_spans[0].attributes)
    assert "continuation-secret" not in str(durable_spans[0].attributes)

    model_calls = await repository.list_model_calls_for_run(run_id)
    assert len(model_calls) == 1
    assert model_calls[0].agent_id == agent_id
    assert model_calls[0].turn == 1
    assert model_calls[0].provider == "deepseek"
    assert model_calls[0].model == "deepseek-v4-flash"
    assert model_calls[0].input_tokens == 10
    assert model_calls[0].output_tokens == 20
    assert exporter.spans[0].name == "model.call"


@pytest.mark.asyncio
async def test_observability_middleware_records_safe_tool_call_span() -> None:
    repository = InMemoryObservabilityRepository()
    exporter = RecordingExporter()
    stack = MiddlewareStack([ObservabilityMiddleware(_facade(repository, exporter))])
    run_id = uuid4()
    agent_id = uuid4()

    async def operation() -> dict[str, Any]:
        call = ToolCall(
            call_id="call-1",
            name="repo.apply_patch",
            arguments_json='{"patch":"private patch body"}',
        )
        turn = ModelTurn(
            assistant=AssistantMessage(tool_calls=[call]),
            stop_reason=StopReason.TOOL_CALLS,
            provider="deepseek",
            model="deepseek-v4-flash",
            usage=ModelUsage(),
        )
        return {
            "model_turn_count": 1,
            "last_turn": turn.model_dump(mode="json"),
            "messages": [
                turn.assistant.model_dump(mode="json"),
                ToolResultMessage(
                    call_id="call-1",
                    content="raw tool result with private patch body",
                ).model_dump(mode="json"),
            ],
        }

    await stack.run_operation(
        MiddlewareStage.WRAP_TOOL_CALL,
        MiddlewareContext(
            run_id=str(run_id),
            agent_id=str(agent_id),
            runtime_route="solo-modifying",
            messages=[SystemMessage(content="prompt text must stay private")],
            metadata={
                "patch": "private patch body",
                "tool_result": "raw tool result",
                "tool_risk": "write",
                "team_root_run_id": "team-root",
            },
        ),
        operation,
    )

    durable_spans = await repository.list_spans_for_run(run_id)
    assert [span.name for span in durable_spans] == ["tool.call"]
    assert durable_spans[0].attributes["runtime_route"] == "solo-modifying"
    assert durable_spans[0].attributes["agent_id"] == str(agent_id)
    assert durable_spans[0].attributes["tool"] == "repo.apply_patch"
    assert durable_spans[0].attributes["call_id"] == "call-1"
    assert durable_spans[0].attributes["tool_risk"] == "write"
    assert durable_spans[0].attributes["team_root_run_id"] == "team-root"
    assert "patch" not in durable_spans[0].attributes
    assert "tool_result" not in durable_spans[0].attributes
    assert "private patch body" not in str(durable_spans[0].attributes)
    assert exporter.spans[0].name == "tool.call"


@pytest.mark.asyncio
async def test_observability_middleware_failure_does_not_change_operation_result() -> (
    None
):
    stack = MiddlewareStack(
        [ObservabilityMiddleware(FailingFacade())]  # type: ignore[arg-type]
    )

    async def operation() -> dict[str, Any]:
        return {"handled": True}

    result = await stack.run_operation(
        MiddlewareStage.WRAP_MODEL_CALL,
        MiddlewareContext(
            run_id=str(uuid4()),
            agent_id=str(uuid4()),
            runtime_route="solo-readonly",
            messages=[SystemMessage(content="prompt text must stay private")],
        ),
        operation,
    )

    assert result == {"handled": True}


class RecordingExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


class FailingFacade:
    def start_span(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("observability unavailable")

    span = start_span


def _facade(
    repository: InMemoryObservabilityRepository,
    exporter: RecordingExporter,
) -> ObservabilityFacade:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return ObservabilityFacade(
        repository=repository,
        tracer=provider.get_tracer("test"),
    )
