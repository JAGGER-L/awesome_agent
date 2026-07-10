import pytest

from awesome_agent.context import (
    OUTPUT_RESERVE_TOKENS,
    ContextBudget,
    calculate_context_budget,
    estimate_messages,
    estimate_text,
)
from awesome_agent.modeling import SystemMessage, UserMessage


def test_default_256k_budget_uses_one_integer_rounding_rule() -> None:
    budget = calculate_context_budget(262_144, 262_144)

    assert budget == ContextBudget(
        effective_total=262_144,
        output_reserve=32_768,
        safety_reserve=26_214,
        effective_input_limit=203_162,
        compression_threshold=162_529,
    )


def test_model_limit_and_user_reduction_take_the_minimum() -> None:
    assert calculate_context_budget(262_144, 128_000).effective_total == 128_000
    assert calculate_context_budget(64_000, 128_000).effective_total == 64_000
    assert calculate_context_budget(128_000, 128_000).safety_reserve == 12_800


def test_too_small_total_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive input"):
        calculate_context_budget(OUTPUT_RESERVE_TOKENS, OUTPUT_RESERVE_TOKENS)


def test_estimator_is_deterministic_conservative_and_monotonic() -> None:
    prose = "hello world"
    non_ascii = "你好世界"
    code = '{"items": [1, 2, 3], "enabled": true}'

    assert estimate_text(prose) == estimate_text(prose)
    assert estimate_text(non_ascii) >= len(non_ascii)
    assert estimate_text(code) >= estimate_text("items 1 2 3 enabled true")
    assert estimate_text(prose + " appended") >= estimate_text(prose)
    assert estimate_text("x") >= 1
    assert estimate_text("") == 0


def test_message_estimate_includes_structural_overhead() -> None:
    messages = (
        SystemMessage(content="policy"),
        UserMessage(content="inspect"),
    )

    assert estimate_messages(messages) > sum(
        estimate_text(message.content) for message in messages
    )
