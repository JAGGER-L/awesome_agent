from __future__ import annotations

from enum import StrEnum


class WebProviderErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_FAILED = "authentication_failed"
    ACCESS_DENIED = "access_denied"
    RATE_LIMITED = "rate_limited"
    USAGE_LIMIT_EXCEEDED = "usage_limit_exceeded"
    PAYG_LIMIT_EXCEEDED = "payg_limit_exceeded"
    REQUEST_REJECTED = "request_rejected"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    CONNECTION_FAILED = "connection_failed"
    MALFORMED_RESPONSE = "malformed_response"


_ERROR_FACTS: dict[WebProviderErrorCode, tuple[str, bool]] = {
    WebProviderErrorCode.INVALID_REQUEST: (
        "The web provider rejected the request.",
        False,
    ),
    WebProviderErrorCode.AUTHENTICATION_FAILED: (
        "The web credential was rejected.",
        False,
    ),
    WebProviderErrorCode.ACCESS_DENIED: (
        "The web provider denied access.",
        False,
    ),
    WebProviderErrorCode.RATE_LIMITED: (
        "The web provider is temporarily rate limited.",
        True,
    ),
    WebProviderErrorCode.USAGE_LIMIT_EXCEEDED: (
        "The web usage limit was reached.",
        False,
    ),
    WebProviderErrorCode.PAYG_LIMIT_EXCEEDED: (
        "The web pay-as-you-go limit was reached.",
        False,
    ),
    WebProviderErrorCode.REQUEST_REJECTED: (
        "The web provider rejected the request.",
        False,
    ),
    WebProviderErrorCode.PROVIDER_UNAVAILABLE: (
        "The web provider is unavailable.",
        True,
    ),
    WebProviderErrorCode.TIMEOUT: (
        "The web request timed out.",
        True,
    ),
    WebProviderErrorCode.CONNECTION_FAILED: (
        "The web provider could not be reached.",
        True,
    ),
    WebProviderErrorCode.MALFORMED_RESPONSE: (
        "The web provider returned an invalid response.",
        False,
    ),
}


class WebProviderError(RuntimeError):
    """A stable, redacted provider-boundary failure."""

    def __init__(self, code: WebProviderErrorCode) -> None:
        message, retryable = _ERROR_FACTS[code]
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


__all__ = ["WebProviderError", "WebProviderErrorCode"]
