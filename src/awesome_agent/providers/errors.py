from __future__ import annotations

import openai

from awesome_agent.modeling.errors import (
    AuthenticationModelError,
    ConnectionModelError,
    ContextLengthModelError,
    InvalidRequestModelError,
    ModelProviderError,
    ProviderProtocolError,
    RateLimitModelError,
    TimeoutModelError,
    TransientModelError,
)
from awesome_agent.modeling.turns import ProviderId


def classify_openai_error(
    error: Exception,
    *,
    provider: ProviderId,
) -> ModelProviderError:
    status_code = getattr(error, "status_code", None)
    lowered = str(error).casefold()
    if isinstance(error, openai.APITimeoutError):
        return TimeoutModelError(
            "Provider request timed out.",
            provider=provider,
        )
    if isinstance(error, openai.APIConnectionError):
        return ConnectionModelError(
            "Provider connection failed.",
            provider=provider,
        )
    if isinstance(error, openai.AuthenticationError):
        return AuthenticationModelError(
            "Provider authentication failed.",
            provider=provider,
            status_code=status_code,
        )
    if isinstance(error, openai.RateLimitError):
        return RateLimitModelError(
            "Provider rate limit exceeded.",
            provider=provider,
            status_code=status_code,
        )
    if isinstance(error, openai.BadRequestError):
        if "context" in lowered and ("length" in lowered or "token" in lowered):
            return ContextLengthModelError(
                "Provider context limit exceeded.",
                provider=provider,
                status_code=status_code,
            )
        return InvalidRequestModelError(
            "Provider rejected the request.",
            provider=provider,
            status_code=status_code,
        )
    if isinstance(error, openai.APIStatusError) and status_code is not None:
        if status_code >= 500:
            return TransientModelError(
                "Provider service is temporarily unavailable.",
                provider=provider,
                status_code=status_code,
            )
        return InvalidRequestModelError(
            "Provider rejected the request.",
            provider=provider,
            status_code=status_code,
        )
    return ProviderProtocolError(
        "Provider returned an unexpected response.",
        provider=provider,
        status_code=status_code,
    )
