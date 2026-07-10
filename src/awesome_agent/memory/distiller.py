from __future__ import annotations

import json
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
    ModelRequest,
    ModelTurn,
    ModelUsage,
    SelectedModel,
    SystemMessage,
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
    async def complete(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> ModelTurn: ...


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
    ) -> DistillationResult:
        normalized_user = user_text.strip()
        normalized_answer = final_answer.strip()
        if remaining_model_calls < 1 or not normalized_user or not normalized_answer:
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
            turn = await self._gateway.complete(selected_model, request)
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
