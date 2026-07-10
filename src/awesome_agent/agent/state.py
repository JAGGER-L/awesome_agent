from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, Field, JsonValue, TypeAdapter, with_config
from typing_extensions import TypedDict


@with_config(ConfigDict(extra="forbid"))
class AgentState(TypedDict):
    thread_id: str
    turn_id: str
    workspace_key: str
    provider: str
    model: str
    thinking_enabled: bool
    context_manifest: list[dict[str, JsonValue]]
    context_estimated_tokens: int
    context_effective_limit: int
    compression_requested: bool
    compression_reason: str | None
    messages: list[dict[str, JsonValue]]
    continuation: dict[str, JsonValue] | None
    pending_tool_calls: list[dict[str, JsonValue]]
    next_tool_index: int
    tool_results: list[dict[str, JsonValue]]
    model_calls: int
    tool_calls: int
    provider_retries: int
    compressions: int
    active_execution_seconds: float
    usage: dict[str, int]
    recovery_issue: str | None
    final_answer: Annotated[str, Field(max_length=200_000)] | None
    termination_reason: str | None


_STATE_ADAPTER: TypeAdapter[AgentState] = TypeAdapter(AgentState)


def new_agent_state(
    *,
    thread_id: str,
    turn_id: str,
    workspace_key: str,
    provider: str,
    model: str,
    thinking_enabled: bool,
) -> AgentState:
    return AgentState(
        thread_id=thread_id,
        turn_id=turn_id,
        workspace_key=workspace_key,
        provider=provider,
        model=model,
        thinking_enabled=thinking_enabled,
        context_manifest=[],
        context_estimated_tokens=0,
        context_effective_limit=0,
        compression_requested=False,
        compression_reason=None,
        messages=[],
        continuation=None,
        pending_tool_calls=[],
        next_tool_index=0,
        tool_results=[],
        model_calls=0,
        tool_calls=0,
        provider_retries=0,
        compressions=0,
        active_execution_seconds=0.0,
        usage={},
        recovery_issue=None,
        final_answer=None,
        termination_reason=None,
    )


def validate_agent_state(value: object) -> AgentState:
    return _STATE_ADAPTER.validate_python(value)
