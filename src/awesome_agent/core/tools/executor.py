from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, JsonValue, ValidationError

from awesome_agent.core.events import EventType, ToolResultPayload, ToolStartedPayload
from awesome_agent.core.tools.command_policy import (
    CommandPolicyAction,
    evaluate_command,
)
from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import (
    ToolActivityDraft,
    ToolError,
    ToolErrorCode,
    ToolRequest,
    ToolResult,
    ToolStatus,
)
from awesome_agent.core.tools.errors import (
    ExpectedToolFailure,
    ToolInvariantError,
)
from awesome_agent.core.tools.permissions import (
    PermissionPolicy,
    PolicyAction,
    PolicyRequest,
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolCapability,
)
from awesome_agent.core.tools.registry import ToolRegistry

type ToolOutcome = Literal["success", "error", "cancelled"]
type ToolTerminalEventType = Literal[
    EventType.TOOL_COMPLETED,
    EventType.TOOL_FAILED,
    EventType.TOOL_CANCELLED,
]


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        timeout_seconds: float = 30.0,
        max_content_chars: int = 30_000,
        permission_policy: PermissionPolicy | None = None,
    ) -> None:
        self._registry = registry
        self._timeout_seconds = timeout_seconds
        self._max_content_chars = max_content_chars
        self._permission_policy = permission_policy or PermissionPolicy()

    async def execute(
        self,
        request: ToolRequest,
        *,
        context: ToolExecutionContext,
    ) -> ToolResult:
        started = context.monotonic()
        registered = self._registry.resolve(request.tool_name)
        await context.emitter.emit(
            ToolStartedPayload(
                call_id=request.call_id,
                tool_name=request.tool_name,
            ),
            thread_id=context.thread_id,
            turn_id=context.turn_id,
            operation_id=context.operation_id,
        )
        if registered is None:
            return await self._error_result(
                request,
                context,
                started,
                ToolErrorCode.NOT_FOUND,
                "Unknown tool.",
            )
        try:
            arguments = registered.input_model.model_validate(request.arguments)
            hard_deny_reason = self._hard_deny_reason(
                registered.spec.capability,
                arguments,
                context,
            )
            decision = self._permission_policy.evaluate(
                PolicyRequest(
                    capability=registered.spec.capability,
                    mode=context.permission_session.mode,
                    granted_capabilities=frozenset(
                        context.permission_session.granted_capabilities
                    ),
                    hard_deny_reason=hard_deny_reason,
                )
            )
            if decision.action is PolicyAction.DENY:
                return await self._error_result(
                    request,
                    context,
                    started,
                    ToolErrorCode.PERMISSION_DENIED,
                    decision.reason,
                )
            if decision.action is PolicyAction.ASK:
                resolver = context.approval_resolver
                if resolver is None:
                    return await self._error_result(
                        request,
                        context,
                        started,
                        ToolErrorCode.PERMISSION_DENIED,
                        "This tool operation requires approval.",
                    )
                approval = await resolver(
                    self._approval_request(
                        request,
                        registered.spec.capability,
                        context,
                    )
                )
                if approval is ToolApprovalDecision.DENY:
                    return await self._error_result(
                        request,
                        context,
                        started,
                        ToolErrorCode.PERMISSION_DENIED,
                        "Tool operation was denied.",
                    )
                if approval is ToolApprovalDecision.ALLOW_THREAD_WRITES:
                    if registered.spec.capability != ToolCapability.WORKSPACE_WRITE:
                        raise ToolInvariantError(
                            "Thread write approval cannot grant another capability."
                        )
                    context.permission_session.grant_thread_writes()
            async with asyncio.timeout(self._timeout_seconds):
                output = await registered.handler(arguments, context)
        except asyncio.CancelledError:
            await self._finalize(
                request,
                context,
                started,
                outcome="cancelled",
                result_summary="cancelled",
                error_code=ToolErrorCode.CANCELLED.value,
                event_type=EventType.TOOL_CANCELLED,
            )
            raise
        except ValidationError:
            return await self._error_result(
                request,
                context,
                started,
                ToolErrorCode.INVALID_ARGUMENTS,
                "Tool arguments did not match the schema.",
            )
        except TimeoutError:
            return await self._error_result(
                request,
                context,
                started,
                ToolErrorCode.TIMEOUT,
                "Tool execution timed out.",
            )
        except ExpectedToolFailure as error:
            return await self._error_result(
                request,
                context,
                started,
                error.code,
                error.message,
                retryable=error.retryable,
                metadata=error.metadata,
            )
        except Exception as error:
            await self._finalize(
                request,
                context,
                started,
                outcome="error",
                result_summary=ToolErrorCode.EXECUTION_FAILED.value,
                error_code=ToolErrorCode.EXECUTION_FAILED.value,
                event_type=EventType.TOOL_FAILED,
            )
            raise ToolInvariantError("Unexpected tool handler failure.") from error

        result = ToolResult(
            call_id=request.call_id,
            tool_name=request.tool_name,
            status=ToolStatus.SUCCESS,
            content=output.content[: self._max_content_chars],
            metadata=output.metadata,
        )
        await self._finalize(
            request,
            context,
            started,
            outcome="success",
            result_summary="Tool execution completed.",
            error_code=None,
            event_type=EventType.TOOL_COMPLETED,
        )
        return result

    @staticmethod
    def _hard_deny_reason(
        capability: str,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> str | None:
        if capability != ToolCapability.SHELL_EXECUTE:
            return None
        command = getattr(arguments, "command", None)
        if not isinstance(command, str):
            return "Shell tool arguments do not contain a valid command."
        decision = evaluate_command(command, context.workspace.canonical_path)
        return (
            decision.reason
            if decision.action is CommandPolicyAction.DENY
            else None
        )

    @staticmethod
    def _approval_request(
        request: ToolRequest,
        capability: str,
        context: ToolExecutionContext,
    ) -> ToolApprovalRequest:
        path = request.arguments.get("path")
        if request.tool_name == "write_file" and isinstance(path, str):
            requested = Path(path)
            safe_relative = not requested.is_absolute() and ".." not in requested.parts
            exists = (
                (context.workspace.canonical_path / requested).exists()
                if safe_relative
                else False
            )
            operation, target = ("overwrite" if exists else "create"), path
        elif request.tool_name == "edit_file" and isinstance(path, str):
            operation, target = "edit", path
        elif request.tool_name == "delete" and isinstance(path, str):
            operation, target = "delete", path
        elif request.tool_name == "execute":
            command = request.arguments.get("command")
            operation = "run"
            target = command if isinstance(command, str) else "command"
        else:
            operation, target = "use", request.tool_name
        return ToolApprovalRequest(
            capability=capability,
            operation=operation,
            target=target,
            prompt=f"Do you want to {operation} {target}?",
        )

    async def _error_result(
        self,
        request: ToolRequest,
        context: ToolExecutionContext,
        started: float,
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
        await self._finalize(
            request,
            context,
            started,
            outcome="error",
            result_summary=code.value,
            error_code=code.value,
            event_type=EventType.TOOL_FAILED,
        )
        return result

    async def _finalize(
        self,
        request: ToolRequest,
        context: ToolExecutionContext,
        started: float,
        *,
        outcome: ToolOutcome,
        result_summary: str,
        error_code: str | None,
        event_type: ToolTerminalEventType,
    ) -> None:
        duration_ms = max(0, round((context.monotonic() - started) * 1_000))
        argument_names = ", ".join(
            name[:100] for name in sorted(request.arguments)[:16]
        )
        input_summary = (
            f"arguments: {argument_names}" if argument_names else "arguments: none"
        )
        try:
            context.activity_writer.finalize(
                ToolActivityDraft(
                    thread_id=context.thread_id,
                    turn_id=context.turn_id,
                    operation_id=context.operation_id,
                    call_id=request.call_id,
                    origin=context.origin,
                    tool_name=request.tool_name,
                    outcome=outcome,
                    input_summary=input_summary,
                    result_summary=result_summary,
                    error_code=error_code,
                    duration_ms=duration_ms,
                    change_set_id=context.change_set_id,
                )
            )
        except Exception as error:
            raise ToolInvariantError("Tool audit finalization failed.") from error
        await context.emitter.emit(
            ToolResultPayload(
                kind=event_type,
                call_id=request.call_id,
                tool_name=request.tool_name,
                summary=result_summary,
                error_code=error_code,
            ),
            thread_id=context.thread_id,
            turn_id=context.turn_id,
            operation_id=context.operation_id,
        )
