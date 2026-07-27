from __future__ import annotations

from awesome_agent.core.tools.contracts import ToolErrorCode
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.web_errors import (
    WebProviderError,
    WebProviderErrorCode,
)


def web_provider_failure(error: WebProviderError) -> ExpectedToolFailure:
    if error.code in {
        WebProviderErrorCode.INVALID_REQUEST,
        WebProviderErrorCode.REQUEST_REJECTED,
    }:
        tool_code = ToolErrorCode.WEB_REQUEST_REJECTED
    elif error.code in {
        WebProviderErrorCode.AUTHENTICATION_FAILED,
        WebProviderErrorCode.ACCESS_DENIED,
    }:
        tool_code = ToolErrorCode.WEB_CREDENTIAL_REJECTED
    elif error.code is WebProviderErrorCode.RATE_LIMITED:
        tool_code = ToolErrorCode.WEB_RATE_LIMITED
    elif error.code in {
        WebProviderErrorCode.USAGE_LIMIT_EXCEEDED,
        WebProviderErrorCode.PAYG_LIMIT_EXCEEDED,
    }:
        tool_code = ToolErrorCode.WEB_QUOTA_EXHAUSTED
    elif error.code is WebProviderErrorCode.PROVIDER_UNAVAILABLE:
        tool_code = ToolErrorCode.WEB_PROVIDER_UNAVAILABLE
    elif error.code is WebProviderErrorCode.TIMEOUT:
        tool_code = ToolErrorCode.WEB_TIMEOUT
    elif error.code is WebProviderErrorCode.CONNECTION_FAILED:
        tool_code = ToolErrorCode.WEB_CONNECTION_FAILED
    elif error.code is WebProviderErrorCode.MALFORMED_RESPONSE:
        tool_code = ToolErrorCode.WEB_MALFORMED_RESPONSE
    else:
        raise RuntimeError("Unhandled web provider error code")
    return ExpectedToolFailure(
        tool_code,
        error.message,
        retryable=error.retryable,
    )


__all__ = ["web_provider_failure"]
