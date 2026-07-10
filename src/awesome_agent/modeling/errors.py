from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from awesome_agent.modeling.turns import ProviderId


class ModelErrorCode(StrEnum):
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    INVALID_REQUEST = "invalid_request"
    CONTEXT_LENGTH = "context_length"
    PROVIDER_PROTOCOL = "provider_protocol"


_RETRYABLE_CODES = {
    ModelErrorCode.CONNECTION,
    ModelErrorCode.TIMEOUT,
    ModelErrorCode.RATE_LIMIT,
    ModelErrorCode.TRANSIENT,
}


class ModelErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ModelErrorCode
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool
    provider: ProviderId
    status_code: int | None = Field(default=None, ge=100, le=599)

    @model_validator(mode="after")
    def validate_retryability(self) -> Self:
        if self.retryable is not (self.code in _RETRYABLE_CODES):
            raise ValueError("Model error retryability does not match its code.")
        return self


class ModelProviderError(Exception):
    code = ModelErrorCode.PROVIDER_PROTOCOL
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        provider: ProviderId,
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


class ConnectionModelError(ModelProviderError):
    code = ModelErrorCode.CONNECTION
    retryable = True


class TimeoutModelError(ModelProviderError):
    code = ModelErrorCode.TIMEOUT
    retryable = True


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
    ModelErrorCode.CONNECTION: ConnectionModelError,
    ModelErrorCode.TIMEOUT: TimeoutModelError,
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
