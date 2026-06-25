from __future__ import annotations

import openai

from awesome_agent.modeling.errors import (
    AuthenticationModelError,
    ContextLengthModelError,
    InvalidRequestModelError,
    ModelProviderError,
    ProviderProtocolError,
    RateLimitModelError,
    TransientModelError,
)


def classify_openai_error(
    error: Exception,
    *,
    provider: str,
) -> ModelProviderError:
    status_code = getattr(error, "status_code", None)
    message = str(error)
    lowered = message.lower()
    if isinstance(error, openai.AuthenticationError):
        return AuthenticationModelError(
            message,
            provider=provider,
            status_code=status_code,
        )
    if isinstance(error, openai.RateLimitError):
        return RateLimitModelError(
            message,
            provider=provider,
            status_code=status_code,
        )
    if isinstance(error, (openai.APIConnectionError, openai.APITimeoutError)):
        return TransientModelError(
            message,
            provider=provider,
            status_code=status_code,
        )
    if isinstance(error, openai.BadRequestError):
        if "context" in lowered and ("length" in lowered or "token" in lowered):
            return ContextLengthModelError(
                message,
                provider=provider,
                status_code=status_code,
            )
        return InvalidRequestModelError(
            message,
            provider=provider,
            status_code=status_code,
        )
    if isinstance(error, openai.APIStatusError) and status_code is not None:
        if status_code >= 500:
            return TransientModelError(
                message,
                provider=provider,
                status_code=status_code,
            )
        return InvalidRequestModelError(
            message,
            provider=provider,
            status_code=status_code,
        )
    return ProviderProtocolError(
        message,
        provider=provider,
        status_code=status_code,
    )
