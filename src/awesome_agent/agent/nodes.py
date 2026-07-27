from __future__ import annotations

import asyncio
import json
from typing import cast

from langgraph.runtime import Runtime
from pydantic import JsonValue, TypeAdapter, ValidationError

from awesome_agent.agent.budgets import (
    BudgetDecision,
    add_active_segment,
    charge_compression,
    charge_model_attempt,
    charge_provider_retry,
    charge_tool_call,
    loop_exhaustion,
    model_call_decision,
)
from awesome_agent.agent.context import AgentRuntimeContext
from awesome_agent.agent.finalization import (
    PostAnswerDiagnostic,
    PostAnswerFinalizationRequest,
    PostAnswerFinalizationResult,
    collect_tool_citations,
    validate_answer_citations,
)
from awesome_agent.agent.state import AgentState
from awesome_agent.core.tools import (
    ToolError,
    ToolErrorCode,
    ToolRequest,
    ToolResult,
    ToolStatus,
)
from awesome_agent.modeling import (
    ContinuationState,
    ModelMessage,
    ModelProviderError,
    ModelRequest,
    ModelTurn,
    ModelUsage,
    ProviderId,
    ProviderRetrying,
    SelectedModel,
    TextDelta,
    ToolCall,
    ToolChoice,
    ToolChoiceMode,
    ToolDefinition,
    ToolResultMessage,
    TurnCompleted,
    TurnFailed,
)

_MESSAGES: TypeAdapter[tuple[ModelMessage, ...]] = TypeAdapter(tuple[ModelMessage, ...])


async def prepare_context(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
) -> AgentState:
    if state["messages"]:
        return state
    context = _context(runtime)
    prepared = await context.context_builder(state)
    updated = _copy(state)
    updated["messages"] = [_message_dict(message) for message in prepared.messages]
    updated["context_manifest"] = list(prepared.manifest)
    updated["context_estimated_tokens"] = prepared.estimated_input_tokens
    updated["context_effective_limit"] = prepared.effective_input_limit
    updated["compression_requested"] = prepared.compression_recommended
    updated["compression_reason"] = (
        "automatic" if prepared.compression_recommended else None
    )
    await context.context_snapshot_recorder(prepared.manifest)
    await context.event_projector.project_context(
        source_count=len(prepared.manifest),
        estimated_tokens=prepared.estimated_input_tokens,
        compressed=False,
    )
    return updated


async def compress_context(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
) -> AgentState:
    context = _context(runtime)
    updated = _copy(state)
    reason = updated["compression_reason"] or "automatic"
    if updated["model_calls"] >= context.budget.model_calls - 1:
        updated["compression_requested"] = False
        updated["compression_reason"] = None
        if reason == "context_length":
            updated["termination_reason"] = "context_unrecoverable"
        else:
            await context.event_projector.project_warning(
                code="model_budget_reserved",
                message="The final model call is reserved; compression was skipped.",
            )
        return updated
    if updated["compressions"] >= context.budget.compressions:
        updated["compression_requested"] = False
        updated["compression_reason"] = None
        if reason == "context_length":
            updated["termination_reason"] = "context_unrecoverable"
        else:
            await context.event_projector.project_warning(
                code="compression_budget_exhausted",
                message="Context compression budget is exhausted.",
            )
        return updated
    max_provider_retries = min(
        max(0, context.budget.provider_retries - updated["provider_retries"]),
        max(0, context.budget.model_calls - updated["model_calls"] - 2),
    )
    result = await context.compressor.compress(
        updated,
        max_provider_retries=max_provider_retries,
    )
    if result.attempted:
        updated = charge_compression(updated)
        updated = charge_model_attempt(
            updated,
            provider_retries=result.usage.provider_retries,
        )
        updated["usage"] = _merge_usage(updated["usage"], result.usage)
    updated["compression_requested"] = False
    updated["compression_reason"] = None
    if result.completed and result.prepared is not None:
        prepared = result.prepared
        updated["messages"] = [_message_dict(message) for message in prepared.messages]
        updated["context_manifest"] = list(prepared.manifest)
        updated["context_estimated_tokens"] = prepared.estimated_input_tokens
        updated["context_effective_limit"] = prepared.effective_input_limit
        await context.context_snapshot_recorder(prepared.manifest)
        await context.event_projector.project_context(
            source_count=len(prepared.manifest),
            estimated_tokens=prepared.estimated_input_tokens,
            compressed=True,
        )
        return updated
    error_code = result.error_code or "compression_unavailable"
    if (
        error_code == "context_unrecoverable"
        or reason == "context_length"
        or (
            updated["context_effective_limit"] > 0
            and updated["context_estimated_tokens"] > updated["context_effective_limit"]
        )
    ):
        updated["termination_reason"] = "context_unrecoverable"
        return updated
    await context.event_projector.project_warning(
        code=error_code,
        message="Context compression failed; existing context still fits.",
    )
    return updated


async def call_model(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
) -> AgentState:
    context = _context(runtime)
    updated = _copy(state)
    if updated["next_tool_index"] >= len(updated["pending_tool_calls"]):
        updated["pending_tool_calls"] = []
        updated["next_tool_index"] = 0
    exhaustion = loop_exhaustion(updated, context.budget)
    decision = model_call_decision(
        updated,
        context.budget,
        tools_requested=bool(context.tool_catalog()),
    )
    if exhaustion is not None and updated["model_calls"] < context.budget.model_calls:
        decision = BudgetDecision(
            allowed=True,
            tools_enabled=False,
            reserved_final=True,
        )
    if not decision.allowed:
        updated["termination_reason"] = (
            decision.termination_reason or exhaustion or "budget_exhausted"
        )
        return updated
    if decision.reserved_final:
        updated["termination_reason"] = exhaustion or "budget_final_response"
    tools = (
        tuple(
            ToolDefinition(
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
            )
            for spec in context.tool_catalog()
        )
        if decision.tools_enabled
        else ()
    )
    request = ModelRequest(
        messages=_MESSAGES.validate_python(updated["messages"]),
        tools=tools,
        tool_choice=ToolChoice(
            mode=(ToolChoiceMode.AUTO if tools else ToolChoiceMode.NONE)
        ),
        thinking_enabled=updated["thinking_enabled"],
        continuation=(
            ContinuationState.model_validate(updated["continuation"])
            if updated["continuation"] is not None
            else None
        ),
    )
    started = context.monotonic()
    updated = charge_model_attempt(updated, provider_retries=0)
    completed: list[ModelTurn] = []
    failure: TurnFailed | None = None
    visible_text: list[str] = []
    observed_retries = 0
    retry_blocked: str | None = None
    try:
        async for event in context.gateway.stream(
            SelectedModel(
                provider=cast(ProviderId, updated["provider"]),
                model=updated["model"],
            ),
            request,
        ):
            if isinstance(event, ProviderRetrying):
                if updated["model_calls"] >= context.budget.model_calls:
                    retry_blocked = "model_budget_exhausted"
                    break
                if updated["provider_retries"] >= context.budget.provider_retries:
                    retry_blocked = "provider_retry_budget_exhausted"
                    break
                updated = charge_provider_retry(updated)
                observed_retries += 1
            await context.event_projector.project_gateway(event)
            if isinstance(event, TextDelta):
                visible_text.append(event.text)
            elif isinstance(event, TurnCompleted):
                completed.append(event.turn)
            elif isinstance(event, TurnFailed):
                failure = event
    except ModelProviderError as error:
        failure = TurnFailed(error=error.info)
    ended = context.monotonic()
    updated = add_active_segment(updated, started_at=started, ended_at=ended)
    if retry_blocked is not None:
        updated["final_answer"] = "".join(visible_text) or updated["final_answer"]
        updated["termination_reason"] = retry_blocked
        return updated
    if failure is not None:
        if failure.error.code.value == "context_length":
            updated["compression_requested"] = True
            updated["compression_reason"] = "context_length"
            updated["termination_reason"] = None
            return updated
        updated["final_answer"] = "".join(visible_text) or updated["final_answer"]
        updated["termination_reason"] = f"model_{failure.error.code.value}"
        return updated
    if len(completed) != 1:
        updated["final_answer"] = "".join(visible_text) or updated["final_answer"]
        updated["termination_reason"] = "model_provider_protocol"
        return updated
    turn = completed[0]
    if turn.usage.provider_retries != observed_retries:
        updated["final_answer"] = "".join(visible_text) or updated["final_answer"]
        updated["termination_reason"] = "model_provider_protocol"
        return updated
    if not _tool_calls_are_dispatchable_and_fresh(
        turn.assistant.tool_calls,
        updated["tool_results"],
    ):
        updated["final_answer"] = "".join(visible_text) or updated["final_answer"]
        updated["termination_reason"] = "model_provider_protocol"
        return updated
    updated["usage"] = _merge_usage(updated["usage"], turn.usage)
    updated["continuation"] = (
        cast(dict[str, JsonValue], turn.continuation.model_dump(mode="json"))
        if turn.continuation is not None
        else None
    )
    updated["messages"].append(_message_dict(turn.assistant))
    updated["context_estimated_tokens"] += context.context_token_estimator(
        (turn.assistant,)
    )
    if decision.reserved_final:
        updated["pending_tool_calls"] = []
        updated["next_tool_index"] = 0
        updated["final_answer"] = turn.assistant.content or "".join(visible_text)
        return updated
    updated["pending_tool_calls"] = [
        cast(dict[str, JsonValue], call.model_dump(mode="json"))
        for call in turn.assistant.tool_calls
    ]
    updated["next_tool_index"] = 0
    if not turn.assistant.tool_calls:
        updated["final_answer"] = turn.assistant.content or "".join(visible_text)
        updated["termination_reason"] = turn.stop_reason.value
    return updated


async def execute_one_tool(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
) -> AgentState:
    context = _context(runtime)
    updated = _copy(state)
    index = updated["next_tool_index"]
    if index >= len(updated["pending_tool_calls"]):
        return updated
    raw_call = updated["pending_tool_calls"][index]
    call_id = str(raw_call.get("call_id") or "")
    tool_name = str(raw_call.get("name") or "")
    exhaustion = loop_exhaustion(updated, context.budget)
    if exhaustion is not None:
        updated["termination_reason"] = exhaustion
        result = ToolResult(
            call_id=call_id,
            tool_name=tool_name,
            status=ToolStatus.ERROR,
            content=f"Tool call was not executed: {exhaustion}.",
            metadata={"executed": False, "reason": exhaustion},
            error=ToolError(
                code=ToolErrorCode.EXECUTION_FAILED,
                message=f"Tool call was not executed: {exhaustion}.",
            ),
        )
    else:
        arguments_json = str(raw_call.get("arguments_json") or "")
        updated = charge_tool_call(updated)
        try:
            arguments = json.loads(arguments_json)
            if not isinstance(arguments, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            result = ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolStatus.ERROR,
                content="Tool arguments must be a JSON object.",
                error=ToolError(
                    code=ToolErrorCode.INVALID_ARGUMENTS,
                    message="Tool arguments must be a JSON object.",
                ),
            )
        else:
            started = context.monotonic()
            request = ToolRequest(
                call_id=call_id,
                tool_name=tool_name,
                arguments=cast(dict[str, JsonValue], arguments),
            )
            tool_context = await context.tool_context_factory(updated, request)
            result = await context.executor.execute(request, context=tool_context)
            updated["web_requests"] = tool_context.capability_quotas.used(
                "network.read"
            )
            ended = context.monotonic()
            updated = add_active_segment(updated, started_at=started, ended_at=ended)
    updated["tool_results"].append(
        cast(dict[str, JsonValue], result.model_dump(mode="json"))
    )
    updated["citations"] = [
        cast(dict[str, JsonValue], citation.model_dump(mode="json"))
        for citation in collect_tool_citations(updated["tool_results"])
    ]
    observation = ToolResultMessage(
        call_id=result.call_id,
        content=result.content,
        is_error=result.status is ToolStatus.ERROR,
    )
    updated["messages"].append(_message_dict(observation))
    updated["context_estimated_tokens"] += context.context_token_estimator(
        (observation,)
    )
    updated["next_tool_index"] += 1
    return updated


async def finalize(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
) -> AgentState:
    context = _context(runtime)
    updated = _copy(state)
    if updated["final_answer"] and not _failed_termination(updated):
        citations = collect_tool_citations(updated["tool_results"])
        updated["citations"] = [
            cast(dict[str, JsonValue], citation.model_dump(mode="json"))
            for citation in citations
        ]
        active_budget_exhausted = (
            updated["active_execution_seconds"]
            >= context.budget.active_execution_seconds
        )
        remaining_calls = (
            0
            if active_budget_exhausted
            else context.budget.model_calls - updated["model_calls"]
        )
        remaining_retries = (
            context.budget.provider_retries - updated["provider_retries"]
        )
        request = PostAnswerFinalizationRequest(
            user_text=context.current_user_text,
            final_answer=updated["final_answer"],
            citations=citations,
            selected_model=SelectedModel(
                provider=cast(ProviderId, updated["provider"]),
                model=updated["model"],
            ),
            remaining_model_calls=max(0, remaining_calls),
            remaining_provider_retries=max(0, remaining_retries),
            workspace_key=updated["workspace_key"],
        )
        try:
            raw_result = await context.post_answer_finalizer.finalize(request)
            if type(raw_result) is not PostAnswerFinalizationResult:
                raise TypeError("Finalizer returned an invalid result contract.")
            if type(raw_result.usage) is not ModelUsage:
                raise TypeError("Finalizer returned an invalid usage contract.")
            if type(raw_result.diagnostics) is not tuple or any(
                type(diagnostic) is not PostAnswerDiagnostic
                for diagnostic in raw_result.diagnostics
            ):
                raise TypeError("Finalizer returned an invalid diagnostic contract.")
            result = PostAnswerFinalizationResult.model_validate(
                {
                    "final_answer": raw_result.final_answer,
                    "usage": _usage_payload(raw_result.usage),
                    "model_calls": raw_result.model_calls,
                    "diagnostics": tuple(
                        _diagnostic_payload(diagnostic)
                        for diagnostic in raw_result.diagnostics
                    ),
                },
                strict=True,
            )
            _validate_finalizer_budget(result, request)
        except asyncio.CancelledError:
            raise
        except Exception:
            await context.event_projector.project_warning(
                code="answer_finalization_failed",
                message="Optional answer finalization failed.",
            )
        else:
            updated["final_answer"] = result.final_answer
            if result.model_calls:
                updated = charge_model_attempt(
                    updated,
                    provider_retries=result.usage.provider_retries,
                )
                updated["usage"] = _merge_usage(updated["usage"], result.usage)
            for diagnostic in result.diagnostics:
                await context.event_projector.project_warning(
                    code=diagnostic.code,
                    message=diagnostic.message,
                )
        answer = updated["final_answer"]
        if answer is None:
            raise RuntimeError("Answer finalization removed the final answer.")
        validated_answer, citation_diagnostics = validate_answer_citations(
            answer,
            citations,
        )
        updated["final_answer"] = validated_answer
        for diagnostic in citation_diagnostics:
            await context.event_projector.project_warning(
                code=diagnostic.code,
                message=diagnostic.message,
            )
    if updated["termination_reason"] is None:
        updated["termination_reason"] = "completed"
    return updated


def _failed_termination(state: AgentState) -> bool:
    reason = state["termination_reason"] or ""
    return reason.startswith("model_") or reason == "context_unrecoverable"


def _validate_finalizer_budget(
    result: PostAnswerFinalizationResult,
    request: PostAnswerFinalizationRequest,
) -> None:
    retries = result.usage.provider_retries
    if retries > request.remaining_provider_retries:
        raise ValueError("Finalizer exceeded the provider retry budget.")
    if result.model_calls + retries > request.remaining_model_calls:
        raise ValueError("Finalizer exceeded the model call budget.")


def route_after_model(state: AgentState) -> str:
    if state["compression_requested"]:
        return "compress_context"
    if state["termination_reason"] is not None and not state["pending_tool_calls"]:
        return "finalize"
    if state["pending_tool_calls"]:
        return "execute_one_tool"
    return "finalize"


def route_after_prepare(state: AgentState) -> str:
    return "compress_context" if state["compression_requested"] else "call_model"


def route_after_compression(state: AgentState) -> str:
    return "finalize" if state["termination_reason"] is not None else "call_model"


def route_after_tool(state: AgentState) -> str:
    if state["next_tool_index"] < len(state["pending_tool_calls"]):
        return "execute_one_tool"
    return "call_model"


def _context(runtime: Runtime[AgentRuntimeContext]) -> AgentRuntimeContext:
    if runtime.context is None:
        raise RuntimeError("Agent runtime context is required.")
    return runtime.context


def _copy(state: AgentState) -> AgentState:
    updated = cast(AgentState, dict(state))
    updated["context_manifest"] = list(state["context_manifest"])
    updated["messages"] = list(state["messages"])
    updated["pending_tool_calls"] = list(state["pending_tool_calls"])
    updated["tool_results"] = list(state["tool_results"])
    updated["usage"] = dict(state["usage"])
    return updated


def _tool_calls_are_dispatchable_and_fresh(
    calls: tuple[ToolCall, ...],
    prior_results: list[dict[str, JsonValue]],
) -> bool:
    try:
        for call in calls:
            ToolRequest(call_id=call.call_id, tool_name=call.name)
    except ValidationError:
        return False
    call_ids = tuple(call.call_id for call in calls)
    if len(set(call_ids)) != len(call_ids):
        return False
    completed_ids = {
        call_id
        for result in prior_results
        if isinstance((call_id := result.get("call_id")), str)
    }
    return completed_ids.isdisjoint(call_ids)


def _message_dict(message: ModelMessage) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], message.model_dump(mode="json"))


def _merge_usage(current: dict[str, int], usage: ModelUsage) -> dict[str, int]:
    result = dict(current)
    for key, value in usage.model_dump(mode="json").items():
        result[key] = result.get(key, 0) + cast(int, value)
    return result


def _usage_payload(usage: ModelUsage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "provider_retries": usage.provider_retries,
    }


def _diagnostic_payload(diagnostic: PostAnswerDiagnostic) -> dict[str, str]:
    return {
        "code": diagnostic.code,
        "message": diagnostic.message,
    }
