from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ModelErrorCode(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    INVALID_REQUEST = "invalid_request"
    CONTEXT_LENGTH = "context_length"
    PROVIDER_PROTOCOL = "provider_protocol"


class ModelErrorInfo(BaseModel):
    code: ModelErrorCode
    message: str
    retryable: bool
    provider: str
    status_code: int | None = None


class ModelProviderError(Exception):
    code = ModelErrorCode.PROVIDER_PROTOCOL
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.info = ModelErrorInfo(
            code=self.code,
            message=message,
            retryable=self.retryable,
            provider=provider,
            status_code=status_code,
        )


class AuthenticationModelError(ModelProviderError):
    code = ModelErrorCode.AUTHENTICATION


class RateLimitModelError(ModelProviderError):
    code = ModelErrorCode.RATE_LIMIT
    retryable = True


class TransientModelError(ModelProviderError):
    code = ModelErrorCode.TRANSIENT
    retryable = True


class InvalidRequestModelError(ModelProviderError):
    code = ModelErrorCode.INVALID_REQUEST


class ContextLengthModelError(ModelProviderError):
    code = ModelErrorCode.CONTEXT_LENGTH


class ProviderProtocolError(ModelProviderError):
    code = ModelErrorCode.PROVIDER_PROTOCOL


_ERROR_TYPES: dict[ModelErrorCode, type[ModelProviderError]] = {
    ModelErrorCode.AUTHENTICATION: AuthenticationModelError,
    ModelErrorCode.RATE_LIMIT: RateLimitModelError,
    ModelErrorCode.TRANSIENT: TransientModelError,
    ModelErrorCode.INVALID_REQUEST: InvalidRequestModelError,
    ModelErrorCode.CONTEXT_LENGTH: ContextLengthModelError,
    ModelErrorCode.PROVIDER_PROTOCOL: ProviderProtocolError,
}


def error_from_info(info: ModelErrorInfo) -> ModelProviderError:
    error_type = _ERROR_TYPES[info.code]
    return error_type(
        info.message,
        provider=info.provider,
        status_code=info.status_code,
    )
