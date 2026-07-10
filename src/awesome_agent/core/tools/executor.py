from __future__ import annotations

import asyncio

from pydantic import JsonValue, ValidationError

from awesome_agent.core.events import EventType, ToolResultPayload, ToolStartedPayload
from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import (
    ToolError,
    ToolErrorCode,
    ToolRequest,
    ToolResult,
    ToolStatus,
)
from awesome_agent.core.tools.errors import (
    ExpectedToolFailure,
    ToolControlFlow,
    ToolInvariantError,
)
from awesome_agent.core.tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        timeout_seconds: float = 30.0,
        max_content_chars: int = 30_000,
    ) -> None:
        self._registry = registry
        self._timeout_seconds = timeout_seconds
        self._max_content_chars = max_content_chars

    async def execute(
        self,
        request: ToolRequest,
        *,
        context: ToolExecutionContext,
    ) -> ToolResult:
        registered = self._registry.resolve(request.tool_name)
        await context.emitter.emit(
            ToolStartedPayload(
                call_id=request.call_id,
                tool_name=request.tool_name,
            ),
            turn_id=context.turn_id,
            operation_id=context.operation_id,
        )
        if registered is None:
            return await self._error_result(
                request,
                context,
                ToolErrorCode.NOT_FOUND,
                "Unknown tool.",
            )
        try:
            arguments = registered.input_model.model_validate(request.arguments)
            async with asyncio.timeout(self._timeout_seconds):
                output = await registered.handler(arguments, context)
        except ValidationError as error:
            return await self._error_result(
                request,
                context,
                ToolErrorCode.INVALID_ARGUMENTS,
                str(error),
            )
        except TimeoutError:
            return await self._error_result(
                request,
                context,
                ToolErrorCode.TIMEOUT,
                "Tool execution timed out.",
            )
        except ExpectedToolFailure as error:
            return await self._error_result(
                request,
                context,
                error.code,
                error.message,
                retryable=error.retryable,
                metadata=error.metadata,
            )
        except ToolControlFlow:
            raise
        except Exception as error:
            raise ToolInvariantError("Unexpected tool handler failure.") from error

        result = ToolResult(
            call_id=request.call_id,
            tool_name=request.tool_name,
            status=ToolStatus.SUCCESS,
            content=output.content[: self._max_content_chars],
            metadata=output.metadata,
        )
        await context.emitter.emit(
            ToolResultPayload(
                kind=EventType.TOOL_COMPLETED,
                call_id=result.call_id,
                tool_name=result.tool_name,
                summary="Tool execution completed.",
            ),
            turn_id=context.turn_id,
            operation_id=context.operation_id,
        )
        return result

    async def _error_result(
        self,
        request: ToolRequest,
        context: ToolExecutionContext,
        code: ToolErrorCode,
        message: str,
        *,
        retryable: bool = False,
        metadata: dict[str, JsonValue] | None = None,
    ) -> ToolResult:
        bounded = message[:2_000]
        error = ToolError(code=code, message=bounded, retryable=retryable)
        result = ToolResult(
            call_id=request.call_id,
            tool_name=request.tool_name,
            status=ToolStatus.ERROR,
            content=bounded,
            metadata=metadata or {},
            error=error,
        )
        await context.emitter.emit(
            ToolResultPayload(
                kind=EventType.TOOL_FAILED,
                call_id=result.call_id,
                tool_name=result.tool_name,
                summary=error.message,
                error_code=code.value,
            ),
            turn_id=context.turn_id,
            operation_id=context.operation_id,
        )
        return result
