from __future__ import annotations

import json
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from awesome_agent.memory.models import (
    Mem0Diagnostic,
    MemoryCandidate,
    MemoryPolicyStatus,
    MemoryScope,
)
from awesome_agent.memory.policy import CloudMemoryPolicy
from awesome_agent.modeling import (
    GatewayEvent,
    ModelRequest,
    ModelTurn,
    ModelUsage,
    ProviderRetrying,
    SelectedModel,
    SystemMessage,
    TurnCompleted,
    TurnFailed,
    UserMessage,
)
from awesome_agent.safety import redact_text


class DistillationStatus(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    WARNING = "warning"


class DistillationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: DistillationStatus
    candidates: tuple[MemoryCandidate, ...] = Field(default=(), max_length=5)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    model_calls: int = Field(default=0, ge=0, le=1)
    diagnostic: Mem0Diagnostic | None = None


class DistillerGateway(Protocol):
    def stream(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]: ...


class _RawCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MemoryScope
    content: str = Field(min_length=1, max_length=500)


class _DistillationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: tuple[_RawCandidate, ...] = Field(max_length=5)


class MemoryDistiller:
    def __init__(
        self,
        gateway: DistillerGateway,
        *,
        policy: CloudMemoryPolicy | None = None,
    ) -> None:
        self._gateway = gateway
        self._policy = policy or CloudMemoryPolicy()

    async def distill(
        self,
        *,
        user_text: str,
        final_answer: str,
        selected_model: SelectedModel,
        remaining_model_calls: int,
        workspace_key: str,
        remaining_provider_retries: int = 6,
    ) -> DistillationResult:
        normalized_user = user_text.strip()
        normalized_answer = final_answer.strip()
        if (
            remaining_model_calls < 1
            or remaining_provider_retries < 0
            or not normalized_user
            or not normalized_answer
        ):
            return DistillationResult(status=DistillationStatus.SKIPPED)

        safe_user = redact_text(normalized_user[:4_000]).text
        safe_answer = redact_text(normalized_answer[:8_000]).text
        request = ModelRequest(
            messages=(
                SystemMessage(
                    content=(
                        "Extract only stable, reusable user preferences or workspace "
                        "conventions from the two supplied fields. Return strict JSON "
                        'as {"candidates":[{"scope":"user|workspace",'
                        '"content":"..."}]}. Return at most five candidates.'
                    )
                ),
                UserMessage(
                    content=json.dumps(
                        {
                            "current_user_text": safe_user,
                            "final_answer": safe_answer,
                        },
                        ensure_ascii=False,
                    )
                ),
            ),
            tools=(),
            max_output_tokens=2_000,
            thinking_enabled=False,
        )
        try:
            turn = await self._complete_bounded(
                selected_model,
                request,
                remaining_model_calls=remaining_model_calls,
                remaining_provider_retries=remaining_provider_retries,
            )
        except _DistillationBudgetExceeded as error:
            return DistillationResult(
                status=DistillationStatus.WARNING,
                usage=ModelUsage(provider_retries=error.provider_retries),
                model_calls=1,
                diagnostic=Mem0Diagnostic(
                    code="memory_distillation_budget_exceeded",
                    operation="distill",
                ),
            )
        except Exception:
            return DistillationResult(
                status=DistillationStatus.WARNING,
                model_calls=1,
                diagnostic=Mem0Diagnostic(
                    code="memory_distillation_failed",
                    operation="distill",
                ),
            )

        try:
            payload = _DistillationPayload.model_validate_json(turn.assistant.content)
        except (ValidationError, ValueError):
            return DistillationResult(
                status=DistillationStatus.WARNING,
                usage=turn.usage,
                model_calls=1,
                diagnostic=Mem0Diagnostic(
                    code="memory_distillation_invalid",
                    operation="distill",
                ),
            )

        candidates: list[MemoryCandidate] = []
        for raw in payload.candidates:
            result = self._policy.evaluate(
                raw.content,
                scope=raw.scope,
                workspace_key=workspace_key,
            )
            if (
                result.status is MemoryPolicyStatus.ELIGIBLE
                and result.candidate is not None
            ):
                candidates.append(result.candidate)
        return DistillationResult(
            status=DistillationStatus.COMPLETED,
            candidates=tuple(candidates),
            usage=turn.usage,
            model_calls=1,
        )

    async def _complete_bounded(
        self,
        selected_model: SelectedModel,
        request: ModelRequest,
        *,
        remaining_model_calls: int,
        remaining_provider_retries: int,
    ) -> ModelTurn:
        completed: list[ModelTurn] = []
        retries = 0
        async for event in self._gateway.stream(selected_model, request):
            if isinstance(event, ProviderRetrying):
                if (
                    retries >= remaining_provider_retries
                    or 1 + retries >= remaining_model_calls
                ):
                    raise _DistillationBudgetExceeded(retries)
                retries += 1
            elif isinstance(event, TurnCompleted):
                completed.append(event.turn)
            elif isinstance(event, TurnFailed):
                raise RuntimeError("memory distillation provider failure")
        if len(completed) != 1:
            raise RuntimeError("memory distillation provider protocol")
        return completed[0]


class _DistillationBudgetExceeded(RuntimeError):
    def __init__(self, provider_retries: int) -> None:
        self.provider_retries = provider_retries
        super().__init__("memory distillation budget exceeded")
