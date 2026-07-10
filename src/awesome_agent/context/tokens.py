from __future__ import annotations

from math import floor

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.modeling import ModelMessage

OUTPUT_RESERVE_TOKENS = 32_768
SAFETY_RESERVE_FRACTION = 0.10
COMPRESSION_THRESHOLD_FRACTION = 0.80


class ContextBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    effective_total: int = Field(gt=0)
    output_reserve: int = Field(ge=0)
    safety_reserve: int = Field(ge=0)
    effective_input_limit: int = Field(gt=0)
    compression_threshold: int = Field(gt=0)


def calculate_context_budget(
    configured_total: int,
    model_context_limit: int,
) -> ContextBudget:
    if configured_total <= 0 or model_context_limit <= 0:
        raise ValueError("context totals must be positive")
    effective_total = min(configured_total, model_context_limit)
    output_reserve = min(OUTPUT_RESERVE_TOKENS, effective_total)
    safety_reserve = floor(effective_total * SAFETY_RESERVE_FRACTION)
    effective_input = effective_total - output_reserve - safety_reserve
    if effective_input <= 0:
        raise ValueError("context budget must leave positive input capacity")
    return ContextBudget(
        effective_total=effective_total,
        output_reserve=output_reserve,
        safety_reserve=safety_reserve,
        effective_input_limit=effective_input,
        compression_threshold=floor(effective_input * COMPRESSION_THRESHOLD_FRACTION),
    )


def estimate_text(text: str) -> int:
    if not text:
        return 0
    encoded = len(text.encode("utf-8"))
    structural = sum(text.count(character) for character in "{}[]():,;\n\t")
    divisor = 2 if structural * 5 >= len(text) else 3
    return max(1, (encoded + divisor - 1) // divisor)


def estimate_messages(messages: tuple[ModelMessage, ...]) -> int:
    return sum(estimate_text(message.model_dump_json()) + 8 for message in messages)
