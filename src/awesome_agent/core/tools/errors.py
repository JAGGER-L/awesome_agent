from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from awesome_agent.core.tools.contracts import ToolErrorCode


class ExpectedToolFailureDetails(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )

    code: ToolErrorCode
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool
    metadata: dict[str, JsonValue]


class ExpectedToolFailure(Exception):
    def __init__(
        self,
        code: ToolErrorCode,
        message: str,
        *,
        retryable: bool = False,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        details = _expected_failure_details(
            code=code,
            message=message,
            retryable=retryable,
            metadata={} if metadata is None else metadata,
        )
        super().__init__(details.message)
        self.code = details.code
        self.message = details.message
        self.retryable = details.retryable
        self.metadata = details.metadata


def validate_expected_tool_failure(
    error: ExpectedToolFailure,
) -> ExpectedToolFailureDetails:
    try:
        return _expected_failure_details(
            code=error.code,
            message=error.message,
            retryable=error.retryable,
            metadata=error.metadata,
        )
    except Exception:
        raise TypeError("Invalid expected tool failure contract.") from None


def _expected_failure_details(
    *,
    code: object,
    message: object,
    retryable: object,
    metadata: object,
) -> ExpectedToolFailureDetails:
    try:
        return ExpectedToolFailureDetails.model_validate(
            {
                "code": code,
                "message": message,
                "retryable": retryable,
                "metadata": metadata,
            },
            strict=True,
        )
    except (TypeError, ValueError):
        raise TypeError("Invalid expected tool failure contract.") from None


class ToolInvariantError(RuntimeError):
    pass


class DuplicateToolName(ValueError):
    pass
