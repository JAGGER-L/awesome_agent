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
    TransientModelError,
)
from awesome_agent.providers.routing import (
    ModelCallExecutor,
    ModelRouteAttempt,
    ModelRouteCandidate,
    ModelRouteDecision,
    ModelRouteExecutionError,
    ModelRouteRequest,
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


def _decision(*candidates: ModelRouteCandidate) -> ModelRouteDecision:
    return ModelRouteDecision(route_id="route-1", candidates=candidates)


def _request() -> ModelRequest:
    return ModelRequest(messages=[UserMessage(content="hello")])
