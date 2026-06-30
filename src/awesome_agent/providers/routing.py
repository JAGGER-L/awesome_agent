from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from awesome_agent.modeling import ModelProvider, ModelRequest, ModelTurn
from awesome_agent.modeling.errors import ModelProviderError


@dataclass(frozen=True, slots=True)
class ModelRouteCandidate:
    provider: str
    model: str
    reason: str
    max_input_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ModelRouteRequest:
    runtime_route: str
    agent_role: str | None = None
    task_kind: str | None = None
    token_budget_remaining: int | None = None
    required_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelRouteDecision:
    route_id: str
    candidates: tuple[ModelRouteCandidate, ...]


@dataclass(frozen=True, slots=True)
class ModelRouteAttempt:
    route_id: str
    attempt_number: int
    provider: str
    model: str
    outcome: str
    fallback_reason: str | None = None
    error_code: str | None = None


class ModelRouter(Protocol):
    def resolve(self, request: ModelRouteRequest) -> ModelRouteDecision:
        ...


class TokenBudgetCheck(Protocol):
    def __call__(
        self,
        candidate: ModelRouteCandidate,
        request: ModelRequest,
    ) -> None:
        ...


class TokenUsageRecorder(Protocol):
    def __call__(
        self,
        candidate: ModelRouteCandidate,
        turn: ModelTurn,
    ) -> None:
        ...


AttemptRecorder = Callable[[ModelRouteAttempt], None]
ProviderFactory = Callable[[ModelRouteCandidate], ModelProvider]


class StaticModelRouter:
    def __init__(
        self,
        *,
        default_candidate: ModelRouteCandidate,
        route_candidates: Mapping[
            tuple[str, str | None],
            tuple[ModelRouteCandidate, ...],
        ]
        | None = None,
    ) -> None:
        self._default_candidate = default_candidate
        self._route_candidates = dict(route_candidates or {})

    def resolve(self, request: ModelRouteRequest) -> ModelRouteDecision:
        candidates = (
            self._route_candidates.get((request.runtime_route, request.agent_role))
            or self._route_candidates.get((request.runtime_route, None))
            or (self._default_candidate,)
        )
        return ModelRouteDecision(
            route_id=_route_id(request, candidates),
            candidates=candidates,
        )


class ModelRouteExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        attempts: tuple[ModelRouteAttempt, ...],
        last_error: Exception,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class ModelCallExecutor:
    def __init__(
        self,
        provider_factory: ProviderFactory,
        *,
        token_budget_check: TokenBudgetCheck | None = None,
        token_usage_recorder: TokenUsageRecorder | None = None,
        attempt_recorder: AttemptRecorder | None = None,
    ) -> None:
        self._provider_factory = provider_factory
        self._token_budget_check = token_budget_check
        self._token_usage_recorder = token_usage_recorder
        self._attempt_recorder = attempt_recorder

    async def complete(
        self,
        decision: ModelRouteDecision,
        request: ModelRequest,
    ) -> ModelTurn:
        attempts: list[ModelRouteAttempt] = []
        for index, candidate in enumerate(decision.candidates, start=1):
            if self._token_budget_check is not None:
                self._token_budget_check(candidate, request)
            provider = self._provider_factory(candidate)
            try:
                turn = await provider.complete(request)
            except ModelProviderError as error:
                attempt = ModelRouteAttempt(
                    route_id=decision.route_id,
                    attempt_number=index,
                    provider=candidate.provider,
                    model=candidate.model,
                    outcome="failed",
                    fallback_reason=(
                        error.info.code.value
                        if error.info.retryable and index < len(decision.candidates)
                        else None
                    ),
                    error_code=error.info.code.value,
                )
                attempts.append(attempt)
                self._record_attempt(attempt)
                if error.info.retryable and index < len(decision.candidates):
                    continue
                raise ModelRouteExecutionError(
                    "Model route execution failed.",
                    attempts=tuple(attempts),
                    last_error=error,
                ) from error
            if self._token_usage_recorder is not None:
                self._token_usage_recorder(candidate, turn)
            attempt = ModelRouteAttempt(
                route_id=decision.route_id,
                attempt_number=index,
                provider=candidate.provider,
                model=candidate.model,
                outcome="completed",
            )
            attempts.append(attempt)
            self._record_attempt(attempt)
            return turn
        raise ModelRouteExecutionError(
            "Model route decision did not contain candidates.",
            attempts=tuple(attempts),
            last_error=RuntimeError("no model route candidates"),
        )

    def _record_attempt(self, attempt: ModelRouteAttempt) -> None:
        if self._attempt_recorder is not None:
            self._attempt_recorder(attempt)


def _route_id(
    request: ModelRouteRequest,
    candidates: tuple[ModelRouteCandidate, ...],
) -> str:
    candidate_key = ",".join(
        f"{candidate.provider}:{candidate.model}" for candidate in candidates
    )
    role = request.agent_role or "default"
    task = request.task_kind or "coding"
    return f"{request.runtime_route}:{role}:{task}:{candidate_key}"
