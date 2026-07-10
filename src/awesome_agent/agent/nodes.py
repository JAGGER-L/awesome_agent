from __future__ import annotations

import json
from typing import cast

from langgraph.runtime import Runtime
from pydantic import JsonValue, TypeAdapter

from awesome_agent.agent.budgets import (
    BudgetDecision,
    add_active_segment,
    budget_exhaustion,
    charge_model_attempt,
    charge_tool_call,
    model_call_decision,
)
from awesome_agent.agent.context import AgentRuntimeContext
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
    SelectedModel,
    TextDelta,
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
    return updated


async def call_model(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
) -> AgentState:
    context = _context(runtime)
    updated = _copy(state)
    exhaustion = budget_exhaustion(updated, context.budget)
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
    completed: list[ModelTurn] = []
    failure: TurnFailed | None = None
    visible_text: list[str] = []
    try:
        async for event in context.gateway.stream(
            SelectedModel(
                provider=cast(ProviderId, updated["provider"]),
                model=updated["model"],
            ),
            request,
        ):
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
    if failure is not None:
        updated["final_answer"] = "".join(visible_text) or updated["final_answer"]
        updated["termination_reason"] = f"model_{failure.error.code.value}"
        return updated
    if len(completed) != 1:
        updated["final_answer"] = "".join(visible_text) or updated["final_answer"]
        updated["termination_reason"] = "model_provider_protocol"
        return updated
    turn = completed[0]
    updated = charge_model_attempt(
        updated,
        provider_retries=turn.usage.provider_retries,
    )
    updated["usage"] = _merge_usage(updated["usage"], turn.usage)
    updated["continuation"] = (
        cast(dict[str, JsonValue], turn.continuation.model_dump(mode="json"))
        if turn.continuation is not None
        else None
    )
    updated["messages"].append(_message_dict(turn.assistant))
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
    exhaustion = budget_exhaustion(updated, context.budget)
    if exhaustion is not None:
        updated["pending_tool_calls"] = []
        updated["next_tool_index"] = 0
        updated["termination_reason"] = exhaustion
        return updated
    raw_call = updated["pending_tool_calls"][index]
    call_id = str(raw_call.get("call_id") or "")
    tool_name = str(raw_call.get("name") or "")
    arguments_json = str(raw_call.get("arguments_json") or "")
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
        updated = charge_tool_call(updated)
        started = context.monotonic()
        result = await context.executor.execute(
            ToolRequest(
                call_id=call_id,
                tool_name=tool_name,
                arguments=cast(dict[str, JsonValue], arguments),
            ),
            context=context.tool_context_factory(updated),
        )
        ended = context.monotonic()
        updated = add_active_segment(updated, started_at=started, ended_at=ended)
    updated["tool_results"].append(
        cast(dict[str, JsonValue], result.model_dump(mode="json"))
    )
    updated["messages"].append(
        _message_dict(
            ToolResultMessage(
                call_id=result.call_id,
                content=result.content,
                is_error=result.status is ToolStatus.ERROR,
            )
        )
    )
    updated["next_tool_index"] += 1
    return updated


async def finalize(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
) -> AgentState:
    del runtime
    updated = _copy(state)
    if updated["termination_reason"] is None:
        updated["termination_reason"] = "completed"
    return updated


def route_after_model(state: AgentState) -> str:
    if state["termination_reason"] is not None and not state["pending_tool_calls"]:
        return "finalize"
    if state["pending_tool_calls"]:
        return "execute_one_tool"
    return "finalize"


def route_after_tool(state: AgentState) -> str:
    if state["next_tool_index"] < len(state["pending_tool_calls"]):
        return "execute_one_tool"
    return "call_model"


def _context(runtime: Runtime[AgentRuntimeContext]) -> AgentRuntimeContext:
    if runtime.context is None:
        raise RuntimeError("Agent runtime context is required.")
    return runtime.context


def _copy(state: AgentState) -> AgentState:
    return cast(AgentState, dict(state))


def _message_dict(message: ModelMessage) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], message.model_dump(mode="json"))


def _merge_usage(current: dict[str, int], usage: ModelUsage) -> dict[str, int]:
    result = dict(current)
    for key, value in usage.model_dump(mode="json").items():
        result[key] = result.get(key, 0) + cast(int, value)
    return result
