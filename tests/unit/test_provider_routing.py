from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import fields

import pytest

from awesome_agent.modeling import (
    AssistantMessage,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    ModelUsage,
    StopReason,
    UserMessage,
)
from awesome_agent.modeling.errors import (
    AuthenticationModelError,
    InvalidRequestModelError,
    TransientModelError,
)
from awesome_agent.modeling.stream import TextDelta, TurnCompleted
from awesome_agent.providers.routing import (
    ModelCallExecutor,
    ModelRouteAttempt,
    ModelRouteCandidate,
    ModelRouteDecision,
    ModelRouteExecutionError,
    ModelRouteRequest,
    RoutedModelProvider,
    StaticModelRouter,
)


def test_static_router_preserves_default_single_candidate() -> None:
    default = ModelRouteCandidate(
        provider="deepseek",
        model="deepseek-chat",
        reason="default",
    )
    router = StaticModelRouter(default_candidate=default)

    decision = router.resolve(ModelRouteRequest(runtime_route="solo-readonly"))

    assert decision.candidates == (default,)
    assert decision.route_id == "solo-readonly:default:coding:deepseek:deepseek-chat"


def test_static_router_returns_ordered_route_and_role_candidates() -> None:
    default = ModelRouteCandidate("deepseek", "default", "default")
    leader = (
        ModelRouteCandidate("openai", "gpt-5", "leader-primary"),
        ModelRouteCandidate("deepseek", "deepseek-chat", "leader-fallback"),
    )
    router = StaticModelRouter(
        default_candidate=default,
        route_candidates={("team-coding", "leader"): leader},
    )

    decision = router.resolve(
        ModelRouteRequest(runtime_route="team-coding", agent_role="leader")
    )

    assert decision.candidates == leader


@pytest.mark.asyncio
async def test_model_call_executor_falls_back_on_transient_failure() -> None:
    attempts: list[ModelRouteAttempt] = []
    decision = _decision(
        ModelRouteCandidate("primary", "model-a", "primary"),
        ModelRouteCandidate("fallback", "model-b", "fallback"),
    )
    executor = ModelCallExecutor(
        lambda candidate: (
            FailingProvider(TransientModelError("temporary", provider="primary"))
            if candidate.provider == "primary"
            else SuccessfulProvider(provider=candidate.provider, model=candidate.model)
        ),
        attempt_recorder=attempts.append,
    )

    turn = await executor.complete(decision, _request())

    assert turn.provider == "fallback"
    assert turn.model == "model-b"
    assert [(item.provider, item.outcome) for item in attempts] == [
        ("primary", "failed"),
        ("fallback", "completed"),
    ]
    assert attempts[0].fallback_reason == "transient"
    assert attempts[0].route_id == "route-1"
    assert attempts[0].attempt_number == 1
    assert attempts[1].attempt_number == 2


@pytest.mark.asyncio
async def test_model_call_executor_does_not_fallback_on_authentication_error() -> None:
    decision = _decision(
        ModelRouteCandidate("primary", "model-a", "primary"),
        ModelRouteCandidate("fallback", "model-b", "fallback"),
    )
    executor = ModelCallExecutor(
        lambda _: FailingProvider(
            AuthenticationModelError("bad key", provider="primary")
        )
    )

    with pytest.raises(ModelRouteExecutionError) as captured:
        await executor.complete(decision, _request())

    assert len(captured.value.attempts) == 1
    assert captured.value.attempts[0].error_code == "authentication"
    assert captured.value.attempts[0].fallback_reason is None


@pytest.mark.asyncio
async def test_model_route_execution_error_preserves_provider_failure_details() -> None:
    decision = _decision(
        ModelRouteCandidate("deepseek", "deepseek-v4-flash", "default")
    )
    executor = ModelCallExecutor(
        lambda _: FailingProvider(
            InvalidRequestModelError(
                "400 Invalid 'tools[0].function.name': string does not match pattern.",
                provider="deepseek",
                status_code=400,
            )
        )
    )

    with pytest.raises(ModelRouteExecutionError) as captured:
        await executor.complete(decision, _request())

    message = str(captured.value)
    assert "deepseek/deepseek-v4-flash" in message
    assert "invalid_request" in message
    assert "status=400" in message
    assert "tools[0].function.name" in message


@pytest.mark.asyncio
async def test_model_call_executor_checks_and_records_token_usage_per_attempt() -> None:
    checked: list[tuple[str, int]] = []
    usage: list[tuple[str, int, int]] = []
    decision = _decision(ModelRouteCandidate("deepseek", "model", "default"))

    def check(candidate: ModelRouteCandidate, request: ModelRequest) -> None:
        checked.append((candidate.provider, len(request.messages)))

    def record(candidate: ModelRouteCandidate, turn: ModelTurn) -> None:
        usage.append(
            (
                candidate.provider,
                turn.usage.input_tokens or 0,
                turn.usage.output_tokens or 0,
            )
        )

    executor = ModelCallExecutor(
        lambda candidate: SuccessfulProvider(
            provider=candidate.provider,
            model=candidate.model,
            usage=ModelUsage(input_tokens=3, output_tokens=5),
        ),
        token_budget_check=check,
        token_usage_recorder=record,
    )

    await executor.complete(decision, _request())

    assert checked == [("deepseek", 1)]
    assert usage == [("deepseek", 3, 5)]


@pytest.mark.asyncio
async def test_routed_model_provider_resolves_and_executes_route() -> None:
    attempts: list[ModelRouteAttempt] = []
    router = StaticModelRouter(
        default_candidate=ModelRouteCandidate("default", "default", "unused"),
        route_candidates={
            ("solo-readonly", "leader"): (
                ModelRouteCandidate("primary", "model-a", "primary"),
                ModelRouteCandidate("fallback", "model-b", "fallback"),
            )
        },
    )
    provider = RoutedModelProvider(
        router=router,
        route_request=ModelRouteRequest(
            runtime_route="solo-readonly",
            agent_role="leader",
        ),
        provider_factory=lambda candidate: (
            FailingProvider(TransientModelError("temporary", provider="primary"))
            if candidate.provider == "primary"
            else SuccessfulProvider(provider=candidate.provider, model=candidate.model)
        ),
        attempt_recorder=attempts.append,
    )

    turn = await provider.complete(_request())

    assert turn.provider == "fallback"
    assert turn.model == "model-b"
    assert [(attempt.provider, attempt.outcome) for attempt in attempts] == [
        ("primary", "failed"),
        ("fallback", "completed"),
    ]
    assert attempts[0].route_id.startswith("solo-readonly:leader:coding")


@pytest.mark.asyncio
async def test_model_call_executor_stream_falls_back_before_visible_output() -> None:
    attempts: list[ModelRouteAttempt] = []
    decision = _decision(
        ModelRouteCandidate("primary", "model-a", "primary"),
        ModelRouteCandidate("fallback", "model-b", "fallback"),
    )
    executor = ModelCallExecutor(
        lambda candidate: (
            StreamingFailBeforeOutputProvider(
                TransientModelError("temporary", provider="primary")
            )
            if candidate.provider == "primary"
            else StreamingSuccessProvider(
                provider=candidate.provider,
                model=candidate.model,
                chunks=("ok",),
            )
        ),
        attempt_recorder=attempts.append,
    )

    events = [event async for event in executor.stream(decision, _request())]

    assert [type(event) for event in events] == [TextDelta, TurnCompleted]
    assert [
        (item.provider, item.outcome, item.fallback_reason) for item in attempts
    ] == [("primary", "failed", "transient"), ("fallback", "completed", None)]
    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.turn.provider == "fallback"
    assert completed.turn.model == "model-b"


@pytest.mark.asyncio
async def test_model_call_executor_stream_does_not_fallback_after_output() -> None:
    attempts: list[ModelRouteAttempt] = []
    decision = _decision(
        ModelRouteCandidate("primary", "model-a", "primary"),
        ModelRouteCandidate("fallback", "model-b", "fallback"),
    )
    executor = ModelCallExecutor(
        lambda candidate: (
            StreamingFailAfterOutputProvider(
                TransientModelError("stream dropped", provider="primary")
            )
            if candidate.provider == "primary"
            else StreamingSuccessProvider(
                provider=candidate.provider,
                model=candidate.model,
                chunks=("fallback",),
            )
        ),
        attempt_recorder=attempts.append,
    )

    received: list[object] = []
    with pytest.raises(ModelRouteExecutionError) as captured:
        async for event in executor.stream(decision, _request()):
            received.append(event)

    assert [type(event) for event in received] == [TextDelta]
    assert len(captured.value.attempts) == 1
    assert captured.value.attempts[0].provider == "primary"
    assert captured.value.attempts[0].fallback_reason is None
    assert [(item.provider, item.outcome) for item in attempts] == [
        ("primary", "failed"),
    ]


@pytest.mark.asyncio
async def test_routed_model_provider_stream_resolves_route() -> None:
    attempts: list[ModelRouteAttempt] = []
    router = StaticModelRouter(
        default_candidate=ModelRouteCandidate("default", "default", "unused"),
        route_candidates={
            ("conversation-turn", "leader"): (
                ModelRouteCandidate("primary", "model-a", "primary"),
                ModelRouteCandidate("fallback", "model-b", "fallback"),
            )
        },
    )
    provider = RoutedModelProvider(
        router=router,
        route_request=ModelRouteRequest(
            runtime_route="conversation-turn",
            agent_role="leader",
            task_kind="conversation",
        ),
        provider_factory=lambda candidate: StreamingSuccessProvider(
            provider=candidate.provider,
            model=candidate.model,
            chunks=("hello",),
        ),
        attempt_recorder=attempts.append,
    )

    events = [event async for event in provider.stream(_request())]

    assert [type(event) for event in events] == [TextDelta, TurnCompleted]
    assert attempts[0].route_id.startswith("conversation-turn:leader:conversation")
    assert attempts[0].provider == "primary"
    assert attempts[0].outcome == "completed"


def test_routing_contract_has_no_monetary_fields() -> None:
    forbidden = {"cost", "price", "amount", "usd", "currency", "money"}

    for model in (ModelRouteCandidate, ModelRouteRequest):
        names = {field.name.lower() for field in fields(model)}
        assert forbidden.isdisjoint(names)


class SuccessfulProvider:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        usage: ModelUsage | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.usage = usage or ModelUsage()

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        raise NotImplementedError

    async def complete(self, request: ModelRequest) -> ModelTurn:
        return ModelTurn(
            assistant=AssistantMessage(content="ok"),
            stop_reason=StopReason.COMPLETED,
            provider=self.provider,
            model=self.model,
            usage=self.usage,
        )


class FailingProvider:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        raise NotImplementedError

    async def complete(self, request: ModelRequest) -> ModelTurn:
        raise self.error


class StreamingSuccessProvider:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        chunks: tuple[str, ...],
        usage: ModelUsage | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.chunks = chunks
        self.usage = usage or ModelUsage(input_tokens=1, output_tokens=len(chunks))

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        for chunk in self.chunks:
            yield TextDelta(text=chunk)
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="".join(self.chunks)),
                stop_reason=StopReason.COMPLETED,
                provider=self.provider,
                model=self.model,
                usage=self.usage,
            )
        )

    async def complete(self, request: ModelRequest) -> ModelTurn:
        return ModelTurn(
            assistant=AssistantMessage(content="".join(self.chunks)),
            stop_reason=StopReason.COMPLETED,
            provider=self.provider,
            model=self.model,
            usage=self.usage,
        )


class StreamingFailBeforeOutputProvider:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        raise self.error
        yield TextDelta(text="unreachable")  # pragma: no cover

    async def complete(self, request: ModelRequest) -> ModelTurn:
        raise self.error


class StreamingFailAfterOutputProvider:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        yield TextDelta(text="partial")
        raise self.error

    async def complete(self, request: ModelRequest) -> ModelTurn:
        raise self.error


def _decision(*candidates: ModelRouteCandidate) -> ModelRouteDecision:
    return ModelRouteDecision(route_id="route-1", candidates=candidates)


def _request() -> ModelRequest:
    return ModelRequest(messages=[UserMessage(content="hello")])
