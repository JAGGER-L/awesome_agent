from __future__ import annotations

import asyncio
from typing import Protocol

from awesome_agent.agent.finalization import (
    PostAnswerFinalizationRequest,
    PostAnswerFinalizationResult,
)
from awesome_agent.memory.distiller import DistillationStatus, MemoryDistiller
from awesome_agent.memory.identity import Mem0Identity
from awesome_agent.memory.mem0_cloud import Mem0CloudAdapter, Mem0CloudError
from awesome_agent.memory.models import Mem0Diagnostic
from awesome_agent.modeling import ModelUsage


class MemoryStatusProjector(Protocol):
    async def __call__(self, *, enabled: bool, status: str) -> None: ...


_OPTIONAL_MEMORY_WARNING = "Optional memory operation did not complete."


class Mem0PostAnswerFinalizer:
    """Memory-owned implementation of the Agent post-answer finalizer port."""

    def __init__(
        self,
        *,
        distiller: MemoryDistiller,
        adapter: Mem0CloudAdapter,
        identity: Mem0Identity,
        project_status: MemoryStatusProjector,
    ) -> None:
        self._distiller = distiller
        self._adapter = adapter
        self._identity = identity
        self._project_status = project_status

    async def finalize(
        self,
        request: PostAnswerFinalizationRequest,
    ) -> PostAnswerFinalizationResult:
        if request.workspace_key != self._identity.workspace_key:
            return await self._result(
                request,
                status="warning",
                diagnostics=(
                    Mem0Diagnostic(
                        code="mem0_scope_mismatch",
                        operation="finalize",
                    ),
                ),
            )
        distilled = await self._distiller.distill(
            user_text=request.user_text,
            final_answer=request.final_answer,
            selected_model=request.selected_model,
            remaining_model_calls=request.remaining_model_calls,
            remaining_provider_retries=request.remaining_provider_retries,
            workspace_key=request.workspace_key,
        )
        diagnostics: list[Mem0Diagnostic] = []
        if distilled.diagnostic is not None:
            diagnostics.append(distilled.diagnostic)
        if distilled.status is not DistillationStatus.COMPLETED:
            return await self._result(
                request,
                status=distilled.status.value,
                usage=distilled.usage,
                model_calls=distilled.model_calls,
                diagnostics=tuple(diagnostics),
            )
        for candidate in distilled.candidates:
            try:
                workspace = (
                    request.workspace_key
                    if candidate.scope.value == "workspace"
                    else None
                )
                if await self._adapter.has_fact_hash(
                    candidate.fact_hash,
                    user_id=self._identity.user_id,
                    scope=candidate.scope,
                    workspace_key=workspace,
                ):
                    continue
                outcome = await self._adapter.add(candidate, self._identity)
                if not outcome.accepted and outcome.diagnostic is not None:
                    diagnostics.append(outcome.diagnostic)
            except Mem0CloudError as error:
                diagnostics.append(error.diagnostic)
            except Exception:
                diagnostics.append(
                    Mem0Diagnostic(code="mem0_unavailable", operation="finalize")
                )
        return await self._result(
            request,
            status="warning" if diagnostics else "completed",
            usage=distilled.usage,
            model_calls=distilled.model_calls,
            diagnostics=tuple(diagnostics),
        )

    async def _result(
        self,
        request: PostAnswerFinalizationRequest,
        *,
        status: str,
        usage: ModelUsage | None = None,
        model_calls: int = 0,
        diagnostics: tuple[Mem0Diagnostic, ...] = (),
    ) -> PostAnswerFinalizationResult:
        payload: dict[str, object] = {
            "final_answer": request.final_answer,
            "model_calls": model_calls,
            "diagnostics": tuple(
                _generic_diagnostic_payload(item) for item in diagnostics
            ),
        }
        if usage is not None:
            if type(usage) is not ModelUsage:
                raise TypeError(
                    "Memory distillation returned an invalid usage contract."
                )
            payload["usage"] = _usage_payload(usage)
        result = PostAnswerFinalizationResult.model_validate(payload, strict=True)
        _validate_budget(result, request)
        try:
            await self._project_status(enabled=True, status=status)
        except asyncio.CancelledError:
            raise
        except Exception:
            return _with_status_diagnostic(
                result,
                code="memory_status_projection_failed",
                message="Optional memory status projection failed.",
            )
        return result


def _generic_diagnostic_payload(diagnostic: Mem0Diagnostic) -> dict[str, str]:
    return {
        "code": diagnostic.code,
        "message": _OPTIONAL_MEMORY_WARNING,
    }


def _validate_budget(
    result: PostAnswerFinalizationResult,
    request: PostAnswerFinalizationRequest,
) -> None:
    retries = result.usage.provider_retries
    if retries > request.remaining_provider_retries:
        raise ValueError("Memory finalizer exceeded the provider retry budget.")
    if result.model_calls + retries > request.remaining_model_calls:
        raise ValueError("Memory finalizer exceeded the model call budget.")


def _with_status_diagnostic(
    result: PostAnswerFinalizationResult,
    *,
    code: str,
    message: str,
) -> PostAnswerFinalizationResult:
    payload: dict[str, object] = {
        "final_answer": result.final_answer,
        "usage": _usage_payload(result.usage),
        "model_calls": result.model_calls,
        "diagnostics": (
            *(
                {"code": diagnostic.code, "message": diagnostic.message}
                for diagnostic in result.diagnostics
            ),
            {"code": code, "message": message},
        ),
    }
    return PostAnswerFinalizationResult.model_validate(payload, strict=True)


def _usage_payload(usage: ModelUsage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "provider_retries": usage.provider_retries,
    }
