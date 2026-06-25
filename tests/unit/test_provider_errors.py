from __future__ import annotations

import httpx
import openai

from awesome_agent.modeling import (
    ContextLengthModelError,
    RateLimitModelError,
)
from awesome_agent.providers.errors import classify_openai_error


def test_rate_limit_error_is_retryable() -> None:
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://example.test"),
    )
    classified = classify_openai_error(
        openai.RateLimitError(
            "limited",
            response=response,
            body=None,
        ),
        provider="openai",
    )

    assert isinstance(classified, RateLimitModelError)
    assert classified.info.retryable


def test_context_length_bad_request_is_not_retryable() -> None:
    response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://example.test"),
    )
    classified = classify_openai_error(
        openai.BadRequestError(
            "maximum context length exceeded",
            response=response,
            body=None,
        ),
        provider="deepseek",
    )

    assert isinstance(classified, ContextLengthModelError)
    assert not classified.info.retryable
