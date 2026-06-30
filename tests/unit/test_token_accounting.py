from __future__ import annotations

from awesome_agent.modeling import (
    AssistantMessage,
    ModelRequest,
    SystemMessage,
    ToolCall,
    ToolDefinition,
    UserMessage,
)
from awesome_agent.runtime.token_accounting import (
    ModelTokenProfile,
    TokenAccountant,
    TokenEstimate,
)


class FakeTokenizer:
    def count_text(self, text: str) -> int:
        return len([part for part in text.split(" ") if part])


def test_accountant_uses_model_specific_tokenizer() -> None:
    accountant = TokenAccountant(
        profiles=[
            ModelTokenProfile(
                provider="openai",
                model_pattern="gpt-*",
                estimator_name="fake-openai-tokenizer",
                tokenizer=FakeTokenizer(),
                message_overhead_tokens=3,
                request_overhead_tokens=2,
                tool_overhead_tokens=4,
                error_margin_ratio=0.0,
            )
        ]
    )

    estimate = accountant.estimate_messages(
        [
            SystemMessage(content="system prompt"),
            UserMessage(content="hello world"),
        ],
        provider="openai",
        model="gpt-4.1",
    )

    assert estimate.tokens == 12
    assert estimate.estimator == "fake-openai-tokenizer"
    assert estimate.provider == "openai"
    assert estimate.model == "gpt-4.1"
    assert estimate.exact is True


def test_unknown_model_uses_conservative_fallback() -> None:
    accountant = TokenAccountant()

    estimate = accountant.estimate_text(
        "abc" * 30,
        provider="unknown",
        model="unknown-model",
    )

    assert estimate.tokens >= 30
    assert estimate.estimator == "heuristic-char-fallback"
    assert estimate.exact is False
    assert estimate.error_margin_ratio >= 0.2


def test_model_request_counts_tools_and_tool_calls() -> None:
    accountant = TokenAccountant(
        profiles=[
            ModelTokenProfile(
                provider="test",
                model_pattern="test-model",
                estimator_name="fake",
                tokenizer=FakeTokenizer(),
                message_overhead_tokens=1,
                request_overhead_tokens=1,
                tool_overhead_tokens=2,
                error_margin_ratio=0.0,
            )
        ]
    )
    request = ModelRequest(
        messages=[
            UserMessage(content="hello world"),
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        call_id="call-1",
                        name="repo.read",
                        arguments_json='{"path":"README.md"}',
                    )
                ],
            ),
        ],
        tools=[
            ToolDefinition(
                name="repo.read",
                description="Read a file from the repository.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            )
        ],
    )

    estimate = accountant.estimate_request(
        request,
        provider="test",
        model="test-model",
    )

    assert (
        estimate.tokens
        > accountant.estimate_messages(
            request.messages,
            provider="test",
            model="test-model",
        ).tokens
    )
    assert estimate.estimator == "fake"


def test_token_estimate_with_margin_rounds_up() -> None:
    estimate = TokenEstimate(
        tokens=10,
        estimator="fixture",
        provider="test",
        model="model",
        exact=False,
        error_margin_ratio=0.25,
    )

    assert estimate.with_margin().tokens == 13
