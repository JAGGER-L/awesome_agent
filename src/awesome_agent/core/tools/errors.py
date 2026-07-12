from __future__ import annotations

from pydantic import JsonValue

from awesome_agent.core.tools.contracts import ToolErrorCode


class ExpectedToolFailure(Exception):
    def __init__(
        self,
        code: ToolErrorCode,
        message: str,
        *,
        retryable: bool = False,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.metadata = metadata or {}


class ToolInvariantError(RuntimeError):
    pass


class DuplicateToolName(ValueError):
    pass
