from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from awesome_agent.agent import AgentState
from awesome_agent.conversation import UsageSummary


class ObservedTurnFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    usage: UsageSummary = Field(default_factory=UsageSummary)
    context_manifest: tuple[dict[str, JsonValue], ...] = ()


def observed_turn_facts(state: AgentState | None) -> ObservedTurnFacts:
    if state is None:
        return ObservedTurnFacts()
    usage = state["usage"]
    return ObservedTurnFacts(
        usage=UsageSummary(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            reasoning_tokens=usage.get("reasoning_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            cache_write_tokens=usage.get("cache_write_tokens", 0),
            model_calls=state["model_calls"],
            tool_calls=state["tool_calls"],
            provider_retries=state["provider_retries"],
            compressions=state["compressions"],
            active_execution_seconds=state["active_execution_seconds"],
        ),
        context_manifest=tuple(state["context_manifest"]),
    )
