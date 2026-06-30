from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Any, TypeVar, cast
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from awesome_agent.modeling import ModelTurn, ToolResultMessage
from awesome_agent.observability.facade import (
    ObservabilityFacade,
    ObservabilitySpanInput,
)
from awesome_agent.observability.repository import DurableModelCall
from awesome_agent.runtime.agent_loop.contracts import (
    MiddlewareContext,
    MiddlewareDecision,
    MiddlewareStage,
)

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")
_TURN_ADAPTER: TypeAdapter[ModelTurn] = TypeAdapter(ModelTurn)
_MODEL_STAGE = MiddlewareStage.WRAP_MODEL_CALL
_TOOL_STAGE = MiddlewareStage.WRAP_TOOL_CALL
_AGENT_STAGE = MiddlewareStage.BEFORE_AGENT


class ObservabilityMiddleware:
    name = "observability"

    def __init__(self, facade: ObservabilityFacade | None) -> None:
        self._facade = facade

    async def handle(
        self,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[MiddlewareDecision]],
    ) -> MiddlewareDecision:
        return await call_next(context)

    async def wrap_stage(
        self,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[ResultT]],
    ) -> ResultT:
        if self._facade is None or stage not in {
            _AGENT_STAGE,
            _MODEL_STAGE,
            _TOOL_STAGE,
        }:
            return await call_next(context)
        run_id = _uuid_or_none(context.run_id)
        if run_id is None:
            return await call_next(context)

        started_at = _now()
        started = monotonic()
        try:
            result = await call_next(context)
        except Exception as error:
            await self._record_failed_stage(
                stage=stage,
                context=context,
                run_id=run_id,
                started_at=started_at,
                duration_ms=_elapsed_ms(started),
                error=error,
            )
            raise

        await self._record_completed_stage(
            stage=stage,
            context=context,
            run_id=run_id,
            result=result,
            started_at=started_at,
            duration_ms=_elapsed_ms(started),
        )
        return result

    async def _record_completed_stage(
        self,
        *,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        run_id: UUID,
        result: object,
        started_at: datetime,
        duration_ms: int,
    ) -> None:
        if stage is _MODEL_STAGE:
            await self._record_model_call(
                context=context,
                run_id=run_id,
                result=result,
                started_at=started_at,
                duration_ms=duration_ms,
            )
            return
        if stage is _TOOL_STAGE:
            await self._record_tool_calls(
                context=context,
                run_id=run_id,
                result=result,
                started_at=started_at,
                duration_ms=duration_ms,
            )
            return
        attributes = _base_attributes(stage, context)
        await self._safe_record_span(
            ObservabilitySpanInput(
                run_id=run_id,
                name="agent.run",
                category="agent",
                status="completed",
                attributes=attributes,
                started_at=started_at,
                ended_at=_now(),
                duration_ms=duration_ms,
            )
        )
        await self._record_stage_metrics(
            run_id=run_id,
            count_name="agent.run.count",
            latency_name="agent.run.latency_ms",
            status="completed",
            duration_ms=duration_ms,
            attributes=attributes,
        )

    async def _record_failed_stage(
        self,
        *,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        run_id: UUID,
        started_at: datetime,
        duration_ms: int,
        error: Exception,
    ) -> None:
        attributes = _base_attributes(stage, context)
        await self._safe_record_span(
            ObservabilitySpanInput(
                run_id=run_id,
                name=_span_name(stage),
                category=_span_category(stage),
                status="failed",
                attributes=attributes,
                started_at=started_at,
                ended_at=_now(),
                duration_ms=duration_ms,
                error=str(error),
            )
        )
        await self._record_stage_metrics(
            run_id=run_id,
            count_name=f"{_span_name(stage)}.count",
            latency_name=f"{_span_name(stage)}.latency_ms",
            status="failed",
            duration_ms=duration_ms,
            attributes=attributes,
        )

    async def _record_model_call(
        self,
        *,
        context: MiddlewareContext,
        run_id: UUID,
        result: object,
        started_at: datetime,
        duration_ms: int,
    ) -> None:
        turn = _turn_from_result(result)
        attributes = _base_attributes(MiddlewareStage.WRAP_MODEL_CALL, context)
        attributes["turn"] = _turn_number(result, context)
        if turn is not None:
            attributes.update(
                {
                    "provider": turn.provider,
                    "model": turn.model,
                    "model.provider": turn.provider,
                    "model.name": turn.model,
                    "stop_reason": turn.stop_reason.value,
                }
            )
        span_id = uuid4().hex[:16]
        span = await self._safe_record_span(
            ObservabilitySpanInput(
                run_id=run_id,
                name="model.call",
                category="model",
                status="completed",
                attributes=attributes,
                durable_span_id=span_id,
                started_at=started_at,
                ended_at=_now(),
                duration_ms=duration_ms,
            )
        )
        trace_id = getattr(span, "trace_id", run_id.hex)
        durable_span_id = getattr(span, "span_id", span_id)
        if turn is None:
            await self._record_stage_metrics(
                run_id=run_id,
                count_name="model.call.count",
                latency_name="model.call.latency_ms",
                status="completed",
                duration_ms=duration_ms,
                attributes=attributes,
            )
            return
        await self._safe_record_model_call(
            DurableModelCall(
                run_id=run_id,
                agent_id=_uuid_or_none(context.agent_id),
                turn=_turn_number(result, context),
                provider=turn.provider,
                model=turn.model,
                status="completed",
                stop_reason=turn.stop_reason.value,
                input_tokens=turn.usage.input_tokens,
                output_tokens=turn.usage.output_tokens,
                reasoning_tokens=turn.usage.reasoning_tokens,
                cache_read_tokens=turn.usage.cache_read_tokens,
                cache_write_tokens=turn.usage.cache_write_tokens,
                latency_ms=duration_ms,
                trace_id=trace_id,
                span_id=durable_span_id,
            )
        )
        await self._record_stage_metrics(
            run_id=run_id,
            count_name="model.call.count",
            latency_name="model.call.latency_ms",
            status="completed",
            duration_ms=duration_ms,
            attributes=attributes,
        )
        await self._record_token_metric(
            run_id=run_id,
            name="model.input_tokens",
            value=turn.usage.input_tokens,
            attributes=attributes,
        )
        await self._record_token_metric(
            run_id=run_id,
            name="model.output_tokens",
            value=turn.usage.output_tokens,
            attributes=attributes,
        )
        await self._record_token_metric(
            run_id=run_id,
            name="model.reasoning_tokens",
            value=turn.usage.reasoning_tokens,
            attributes=attributes,
        )

    async def _record_tool_calls(
        self,
        *,
        context: MiddlewareContext,
        run_id: UUID,
        result: object,
        started_at: datetime,
        duration_ms: int,
    ) -> None:
        turn = _turn_from_result(result)
        calls = list(turn.assistant.tool_calls) if turn is not None else []
        direct_tool_result = result if isinstance(result, ToolResultMessage) else None
        if direct_tool_result is not None:
            attributes = _base_attributes(_TOOL_STAGE, context)
            attributes.setdefault("call_id", direct_tool_result.call_id)
            attributes.setdefault("tool.call_id", direct_tool_result.call_id)
            await self._safe_record_span(
                ObservabilitySpanInput(
                    run_id=run_id,
                    name="tool.call",
                    category="tool",
                    status=("failed" if direct_tool_result.is_error else "completed"),
                    attributes=attributes,
                    started_at=started_at,
                    ended_at=_now(),
                    duration_ms=duration_ms,
                )
            )
            await self._record_stage_metrics(
                run_id=run_id,
                count_name="tool.call.count",
                latency_name="tool.call.latency_ms",
                status=("failed" if direct_tool_result.is_error else "completed"),
                duration_ms=duration_ms,
                attributes=attributes,
            )
            return
        result_status = _tool_result_statuses(result)
        if not calls:
            attributes = _base_attributes(_TOOL_STAGE, context)
            await self._safe_record_span(
                ObservabilitySpanInput(
                    run_id=run_id,
                    name="tool.call",
                    category="tool",
                    status="completed",
                    attributes=attributes,
                    started_at=started_at,
                    ended_at=_now(),
                    duration_ms=duration_ms,
                )
            )
            await self._record_stage_metrics(
                run_id=run_id,
                count_name="tool.call.count",
                latency_name="tool.call.latency_ms",
                status="completed",
                duration_ms=duration_ms,
                attributes=attributes,
            )
            return
        for call in calls:
            status = result_status.get(call.call_id, "completed")
            attributes = _base_attributes(_TOOL_STAGE, context)
            attributes.update(
                {
                    "tool": call.name,
                    "tool.name": call.name,
                    "call_id": call.call_id,
                    "tool.call_id": call.call_id,
                }
            )
            await self._safe_record_span(
                ObservabilitySpanInput(
                    run_id=run_id,
                    name="tool.call",
                    category="tool",
                    status=status,
                    attributes=attributes,
                    started_at=started_at,
                    ended_at=_now(),
                    duration_ms=duration_ms,
                )
            )
            await self._record_stage_metrics(
                run_id=run_id,
                count_name="tool.call.count",
                latency_name="tool.call.latency_ms",
                status=status,
                duration_ms=duration_ms,
                attributes=attributes,
            )

    async def _safe_record_span(
        self,
        span: ObservabilitySpanInput,
    ) -> Any:
        if self._facade is None:
            return None
        try:
            return await self._facade.record_span(span)
        except Exception:
            logger.exception("AgentLoop observability span recording failed.")
            return None

    async def _safe_record_model_call(self, call: DurableModelCall) -> None:
        if self._facade is None:
            return
        try:
            await self._facade.record_model_call(call)
        except Exception:
            logger.exception("AgentLoop observability model-call recording failed.")

    async def _record_stage_metrics(
        self,
        *,
        run_id: UUID,
        count_name: str,
        latency_name: str,
        status: str,
        duration_ms: int,
        attributes: dict[str, object],
    ) -> None:
        metric_attributes = {**attributes, "status": status}
        await self._safe_record_metric(
            run_id=run_id,
            name=count_name,
            value=1,
            unit="1",
            attributes=metric_attributes,
        )
        await self._safe_record_metric(
            run_id=run_id,
            name=latency_name,
            value=duration_ms,
            unit="ms",
            attributes=metric_attributes,
        )

    async def _record_token_metric(
        self,
        *,
        run_id: UUID,
        name: str,
        value: int | None,
        attributes: dict[str, object],
    ) -> None:
        if value is None:
            return
        await self._safe_record_metric(
            run_id=run_id,
            name=name,
            value=value,
            unit="tokens",
            attributes=attributes,
        )

    async def _safe_record_metric(
        self,
        *,
        run_id: UUID,
        name: str,
        value: float,
        unit: str,
        attributes: dict[str, object],
    ) -> None:
        if self._facade is None:
            return
        try:
            await self._facade.record_metric(
                run_id=run_id,
                name=name,
                value=value,
                unit=unit,
                attributes=attributes,
            )
        except Exception:
            logger.exception("AgentLoop observability metric recording failed.")


def _base_attributes(
    stage: MiddlewareStage,
    context: MiddlewareContext,
) -> dict[str, object]:
    runtime_route = context.runtime_route
    if context.trace is not None and context.trace.runtime_route is not None:
        runtime_route = context.trace.runtime_route
    attributes: dict[str, object] = {
        **context.metadata,
        "stage": stage.value,
        "runtime_route": runtime_route,
        "runtime.route": runtime_route,
        "agent_id": context.agent_id,
        "agent.id": context.agent_id,
    }
    if context.trace is not None:
        _set_if_not_none(attributes, "run_id", context.trace.run_id)
        _set_if_not_none(attributes, "run.id", context.trace.run_id)
        _set_if_not_none(attributes, "parent_run_id", context.trace.parent_run_id)
        _set_if_not_none(attributes, "parent_run.id", context.trace.parent_run_id)
        _set_if_not_none(attributes, "trace_id", context.trace.trace_id)
        _set_if_not_none(attributes, "trace.id", context.trace.trace_id)
        _set_if_not_none(attributes, "span_id", context.trace.span_id)
        _set_if_not_none(attributes, "span.id", context.trace.span_id)
    if context.capabilities is not None:
        attributes.setdefault("capability.subject_id", context.capabilities.subject_id)
        attributes.setdefault(
            "capability.subject_kind",
            context.capabilities.subject_kind,
        )
        _set_if_not_none(
            attributes,
            "capability.policy_id",
            context.capabilities.policy_id,
        )
        if context.capabilities.allowed_tool_names:
            attributes.setdefault(
                "capability.allowed_tool_names",
                context.capabilities.allowed_tool_names,
            )
        if context.capabilities.denied_tool_names:
            attributes.setdefault(
                "capability.denied_tool_names",
                context.capabilities.denied_tool_names,
            )
    if context.assignment is not None:
        _set_if_not_none(attributes, "assignment_id", context.assignment.assignment_id)
        _set_if_not_none(attributes, "assignment.id", context.assignment.assignment_id)
        _set_if_not_none(
            attributes,
            "team_root_run_id",
            context.assignment.leader_run_id,
        )
        _set_if_not_none(
            attributes,
            "team.root_run_id",
            context.assignment.leader_run_id,
        )
        _set_if_not_none(attributes, "team_role", context.assignment.role)
        _set_if_not_none(attributes, "agent.role", context.assignment.role)
    if context.budget is not None:
        _set_if_not_none(attributes, "budget.token_limit", context.budget.token_limit)
        attributes.setdefault(
            "budget.input_tokens_used",
            context.budget.input_tokens_used,
        )
        attributes.setdefault(
            "budget.output_tokens_used",
            context.budget.output_tokens_used,
        )
        attributes.setdefault(
            "budget.reasoning_tokens_used",
            context.budget.reasoning_tokens_used,
        )
    if context.handoff is not None:
        _set_if_not_none(attributes, "handoff.id", context.handoff.handoff_id)
        _set_if_not_none(
            attributes,
            "handoff.source_agent",
            context.handoff.source_agent,
        )
        _set_if_not_none(
            attributes,
            "handoff.target_agent",
            context.handoff.target_agent,
        )
        _set_if_not_none(attributes, "handoff.reason", context.handoff.reason)
    if context.error is not None:
        _set_if_not_none(attributes, "error.category", context.error.category)
        _set_if_not_none(attributes, "error.retryable", context.error.retryable)
        _set_if_not_none(attributes, "error.origin", context.error.origin)
    _copy_alias(attributes, "team_root_run_id", "team.root_run_id")
    _copy_alias(attributes, "assignment_id", "assignment.id")
    _copy_alias(attributes, "parent_run_id", "parent_run.id")
    _copy_alias(attributes, "team_role", "agent.role")
    _copy_alias(attributes, "tool", "tool.name")
    _copy_alias(attributes, "call_id", "tool.call_id")
    return attributes


def _set_if_not_none(
    attributes: dict[str, object],
    key: str,
    value: object | None,
) -> None:
    if value is not None:
        attributes.setdefault(key, value)


def _copy_alias(
    attributes: dict[str, object],
    source: str,
    target: str,
) -> None:
    value = attributes.get(source)
    if value is not None:
        attributes.setdefault(target, value)


def _span_name(stage: MiddlewareStage) -> str:
    if stage is _MODEL_STAGE:
        return "model.call"
    if stage is _TOOL_STAGE:
        return "tool.call"
    return "agent.run"


def _span_category(stage: MiddlewareStage) -> str:
    if stage is _MODEL_STAGE:
        return "model"
    if stage is _TOOL_STAGE:
        return "tool"
    return "agent"


def _turn_from_result(result: object) -> ModelTurn | None:
    if isinstance(result, ModelTurn):
        return result
    state = _state_dict(result)
    if state is None or "last_turn" not in state:
        return None
    try:
        return _TURN_ADAPTER.validate_python(state["last_turn"])
    except ValidationError:
        logger.exception("AgentLoop observability could not parse model turn.")
        return None


def _tool_result_statuses(result: object) -> dict[str, str]:
    state = _state_dict(result)
    if state is None:
        return {}
    messages = state.get("messages")
    if not isinstance(messages, list):
        return {}
    statuses: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        call_id = message.get("call_id")
        if isinstance(call_id, str):
            statuses[call_id] = "failed" if message.get("is_error") else "completed"
    return statuses


def _turn_number(result: object, context: MiddlewareContext) -> int:
    state = _state_dict(result)
    if state is not None:
        value = state.get("model_turn_count", 0)
        if isinstance(value, int):
            return value
    for key in ("turn", "attempt"):
        value = context.metadata.get(key)
        if isinstance(value, int):
            return value
    return 0


def _state_dict(result: object) -> dict[str, Any] | None:
    if isinstance(result, dict):
        return cast(dict[str, Any], result)
    return None


def _uuid_or_none(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(UTC)


def _elapsed_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))
