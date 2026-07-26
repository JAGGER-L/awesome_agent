from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, JsonValue, ValidationError

from awesome_agent.core.cancellation import finish_cancellation_safe
from awesome_agent.core.events import EventType, ToolResultPayload, ToolStartedPayload
from awesome_agent.core.tools.command_policy import (
    CommandPolicyAction,
    evaluate_command,
    host_shell_dialect,
)
from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import (
    ToolActivityDraft,
    ToolError,
    ToolErrorCode,
    ToolOutput,
    ToolPresentation,
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
from awesome_agent.core.tools.policy import (
    validate_workspace_path_syntax,
)
from awesome_agent.core.tools.registry import RegisteredTool, ToolRegistry
from awesome_agent.core.workspace.path_syntax import workspace_path_platform

_HANDLER_CANCELLATION_GRACE_SECONDS = 10.0
_HANDLER_TIMEOUT_MAX_CANCELLATION_GRACE_SECONDS = 0.5
_TERMINAL_CANCELLATION_CLEANUP_SECONDS = 10.0

logger = logging.getLogger(__name__)

type ToolOutcome = Literal["success", "error", "cancelled"]
type ToolTerminalEventType = Literal[
    EventType.TOOL_COMPLETED,
    EventType.TOOL_FAILED,
    EventType.TOOL_CANCELLED,
]

_WORKSPACE_PATH_TOOLS = frozenset(
    {"delete", "edit_file", "glob", "grep", "ls", "read_file", "write_file"}
)


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
        started_presentation = self._request_presentation(request)
        await context.emitter.emit(
            ToolStartedPayload(
                call_id=request.call_id,
                tool_name=request.tool_name,
                verb=started_presentation.verb,
                target=started_presentation.target,
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
            self._validate_builtin_workspace_path(request.tool_name, arguments)
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
                raise ExpectedToolFailure(
                    ToolErrorCode.PERMISSION_DENIED,
                    decision.reason,
                )
            if decision.action is PolicyAction.ASK:
                resolver = context.approval_resolver
                if resolver is None:
                    raise ExpectedToolFailure(
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
                    raise ExpectedToolFailure(
                        ToolErrorCode.PERMISSION_DENIED,
                        "Tool operation was denied.",
                    )
                if approval is ToolApprovalDecision.ALLOW_THREAD_WRITES:
                    if registered.spec.capability != ToolCapability.WORKSPACE_WRITE:
                        raise ToolInvariantError(
                            "Thread write approval cannot grant another capability."
                        )
                    context.permission_session.grant_thread_writes()
            total_timeout = (
                registered.timeout_resolver(arguments)
                if registered.timeout_resolver is not None
                else self._timeout_seconds
            )
            if total_timeout <= 0:
                raise ToolInvariantError("Tool timeout must be positive.")
            output = await self._invoke_with_deadline(
                registered,
                arguments,
                context,
                timeout_seconds=total_timeout,
            )
        except asyncio.CancelledError as cancellation:
            presentation, activity = self._terminal_artifacts(
                request,
                context,
                started,
                outcome="cancelled",
                presentation=self._request_presentation(
                    request,
                    outcome="Cancelled",
                    summary="Cancelled",
                ),
                error_code=ToolErrorCode.CANCELLED.value,
            )
            await finish_cancellation_safe(
                self._persist_activity_after_cancellation(context, activity)
            )
            terminal_task = self._start_terminal_event(
                request,
                context,
                presentation,
                activity,
                event_type=EventType.TOOL_CANCELLED,
            )
            await self._finish_terminal_event(terminal_task)
            raise cancellation
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
            await self._finalize_handler_outcome(
                request,
                context,
                started,
                outcome="error",
                presentation=self._request_presentation(
                    request,
                    outcome="Failed",
                    summary=ToolErrorCode.EXECUTION_FAILED.value,
                    detail="Tool execution failed.",
                ),
                error_code=ToolErrorCode.EXECUTION_FAILED.value,
                event_type=EventType.TOOL_FAILED,
            )
            raise ToolInvariantError("Unexpected tool handler failure.") from error

        presentation = output.presentation or self._request_presentation(
            request,
            outcome="Completed",
            summary="Completed",
            detail=output.content[:4_000] or None,
        )
        presentation = await self._finalize_handler_outcome(
            request,
            context,
            started,
            outcome="success",
            presentation=presentation,
            error_code=None,
            event_type=EventType.TOOL_COMPLETED,
        )
        result = ToolResult(
            call_id=request.call_id,
            tool_name=request.tool_name,
            status=ToolStatus.SUCCESS,
            content=output.content[: self._max_content_chars],
            metadata=output.metadata,
            presentation=presentation,
        )
        return result

    @staticmethod
    async def _invoke_with_deadline(
        registered: RegisteredTool,
        arguments: BaseModel,
        context: ToolExecutionContext,
        *,
        timeout_seconds: float,
    ) -> ToolOutput:
        loop = asyncio.get_running_loop()
        overall_deadline = loop.time() + timeout_seconds
        timeout_cleanup_grace = min(
            _HANDLER_TIMEOUT_MAX_CANCELLATION_GRACE_SECONDS,
            timeout_seconds / 2,
        )
        execution_deadline = overall_deadline - timeout_cleanup_grace
        completed_at: float | None = None

        async def invoke() -> ToolOutput:
            nonlocal completed_at
            try:
                return await registered.handler(arguments, context)
            finally:
                completed_at = loop.time()

        handler_task: asyncio.Task[ToolOutput] = asyncio.create_task(
            invoke(),
            name=f"tool-handler:{registered.spec.name}",
        )
        try:
            done, _ = await asyncio.wait(
                (handler_task,),
                timeout=max(0.0, execution_deadline - loop.time()),
            )
        except asyncio.CancelledError:
            await ToolExecutor._cancel_handler_task(
                handler_task,
                grace_seconds=(
                    registered.cancellation_grace_seconds
                    or _HANDLER_CANCELLATION_GRACE_SECONDS
                ),
                propagate_caller_cancellation=False,
            )
            raise
        completed_within_deadline = (
            handler_task in done
            and completed_at is not None
            and completed_at <= execution_deadline
        )
        if not completed_within_deadline:
            await ToolExecutor._cancel_handler_task(
                handler_task,
                grace_seconds=max(0.0, overall_deadline - loop.time()),
                propagate_caller_cancellation=True,
            )
            raise TimeoutError
        return handler_task.result()

    @staticmethod
    def _validate_builtin_workspace_path(
        tool_name: str,
        arguments: BaseModel,
    ) -> None:
        if tool_name not in _WORKSPACE_PATH_TOOLS:
            return
        requested = getattr(arguments, "path", None)
        if isinstance(requested, str):
            validate_workspace_path_syntax(
                requested,
                platform=workspace_path_platform(),
            )

    @staticmethod
    async def _cancel_handler_task(
        handler_task: asyncio.Task[ToolOutput],
        *,
        grace_seconds: float,
        propagate_caller_cancellation: bool,
    ) -> None:
        handler_task.cancel()
        deadline = asyncio.get_running_loop().time() + grace_seconds
        caller_cancellation: asyncio.CancelledError | None = None
        while not handler_task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait((handler_task,), timeout=remaining)
            except asyncio.CancelledError as error:
                caller_cancellation = caller_cancellation or error
                continue
        if not handler_task.done():
            handler_task.add_done_callback(ToolExecutor._consume_handler_task)
        else:
            ToolExecutor._consume_handler_task(handler_task)
        if propagate_caller_cancellation and caller_cancellation is not None:
            raise caller_cancellation

    @staticmethod
    def _consume_handler_task(handler_task: asyncio.Task[ToolOutput]) -> None:
        if handler_task.cancelled():
            return
        try:
            handler_task.exception()
        except Exception:
            return

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
        requested_cwd = getattr(arguments, "cwd", None)
        if not isinstance(requested_cwd, str):
            return "Shell tool arguments do not contain a valid working directory."
        decision = evaluate_command(
            command,
            dialect=host_shell_dialect(),
            cwd=context.workspace.canonical_path / requested_cwd,
            workspace=context.workspace.canonical_path,
        )
        return decision.reason if decision.action is CommandPolicyAction.DENY else None

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
        presentation = await self._finalize_handler_outcome(
            request,
            context,
            started,
            outcome="error",
            presentation=self._request_presentation(
                request,
                outcome="Failed",
                summary=code.value,
                detail=bounded,
            ),
            error_code=code.value,
            event_type=EventType.TOOL_FAILED,
        )
        return ToolResult(
            call_id=request.call_id,
            tool_name=request.tool_name,
            status=ToolStatus.ERROR,
            content=bounded,
            metadata=metadata or {},
            error=error,
            presentation=presentation,
        )

    async def _finalize_handler_outcome(
        self,
        request: ToolRequest,
        context: ToolExecutionContext,
        started: float,
        *,
        outcome: ToolOutcome,
        presentation: ToolPresentation,
        error_code: str | None,
        event_type: ToolTerminalEventType,
    ) -> ToolPresentation:
        measured, activity = self._terminal_artifacts(
            request,
            context,
            started,
            outcome=outcome,
            presentation=presentation,
            error_code=error_code,
        )
        audit_error, audit_cancellation = await finish_cancellation_safe(
            self._capture_activity_error(context, activity)
        )
        if audit_cancellation is not None:
            if audit_error is not None:
                logger.warning(
                    "Tool audit finalization failed after caller cancellation.",
                    exc_info=(
                        type(audit_error),
                        audit_error,
                        audit_error.__traceback__,
                    ),
                )
            terminal_task = self._start_terminal_event(
                request,
                context,
                measured,
                activity,
                event_type=event_type,
            )
            await self._finish_terminal_event(terminal_task)
            raise audit_cancellation
        if audit_error is not None:
            raise ToolInvariantError("Tool audit finalization failed.") from audit_error

        terminal_task = self._start_terminal_event(
            request,
            context,
            measured,
            activity,
            event_type=event_type,
        )
        try:
            await asyncio.shield(terminal_task)
        except asyncio.CancelledError as event_cancellation:
            await self._finish_terminal_event(terminal_task)
            raise event_cancellation
        return measured

    @staticmethod
    async def _finish_terminal_event(
        terminal_task: asyncio.Task[None],
    ) -> None:
        deadline = (
            asyncio.get_running_loop().time() + _TERMINAL_CANCELLATION_CLEANUP_SECONDS
        )
        while not terminal_task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait((terminal_task,), timeout=remaining)
            except asyncio.CancelledError:
                continue

        if not terminal_task.done():
            terminal_task.cancel()
            terminal_task.add_done_callback(ToolExecutor._consume_terminal_task)
            logger.warning(
                "Tool terminal event exceeded its bounded cancellation deadline; "
                "terminal event delivery is uncertain."
            )
            return
        if terminal_task.cancelled():
            logger.warning(
                "Tool terminal event was cancelled after handler completion; "
                "terminal event delivery is uncertain."
            )
            return
        error = terminal_task.exception()
        if error is not None:
            logger.warning(
                "Tool terminal event delivery failed after caller cancellation.",
                exc_info=(type(error), error, error.__traceback__),
            )

    @staticmethod
    def _consume_terminal_task(
        terminal_task: asyncio.Task[None],
    ) -> None:
        if terminal_task.cancelled():
            return
        try:
            terminal_task.exception()
        except Exception:
            return

    @staticmethod
    async def _capture_activity_error(
        context: ToolExecutionContext,
        activity: ToolActivityDraft,
    ) -> Exception | None:
        try:
            await context.activity_writer.finalize(activity)
        except Exception as error:
            return error
        return None

    @staticmethod
    async def _persist_activity_after_cancellation(
        context: ToolExecutionContext,
        activity: ToolActivityDraft,
    ) -> None:
        try:
            await context.activity_writer.finalize(activity)
        except (Exception, asyncio.CancelledError):
            logger.warning(
                "Tool audit finalization failed after cancellation.",
                exc_info=True,
            )

    def _terminal_artifacts(
        self,
        request: ToolRequest,
        context: ToolExecutionContext,
        started: float,
        *,
        outcome: ToolOutcome,
        presentation: ToolPresentation,
        error_code: str | None,
    ) -> tuple[ToolPresentation, ToolActivityDraft]:
        duration_ms = max(0, round((context.monotonic() - started) * 1_000))
        measured = presentation.model_copy(update={"duration_ms": duration_ms})
        argument_names = ", ".join(
            name[:100] for name in sorted(request.arguments)[:16]
        )
        input_summary = (
            f"arguments: {argument_names}" if argument_names else "arguments: none"
        )
        return measured, ToolActivityDraft(
            thread_id=context.thread_id,
            turn_id=context.turn_id,
            operation_id=context.operation_id,
            call_id=request.call_id,
            origin=context.origin,
            tool_name=request.tool_name,
            outcome=outcome,
            input_summary=input_summary,
            result_summary=measured.summary,
            error_code=error_code,
            duration_ms=duration_ms,
            change_set_id=context.change_set_id,
        )

    @staticmethod
    def _start_terminal_event(
        request: ToolRequest,
        context: ToolExecutionContext,
        presentation: ToolPresentation,
        activity: ToolActivityDraft,
        *,
        event_type: ToolTerminalEventType,
    ) -> asyncio.Task[None]:
        async def emit_terminal() -> None:
            await context.emitter.emit(
                ToolResultPayload(
                    kind=event_type,
                    call_id=request.call_id,
                    tool_name=request.tool_name,
                    verb=presentation.verb,
                    target=presentation.target,
                    outcome=presentation.outcome or activity.outcome.title(),
                    summary=presentation.summary,
                    detail=presentation.detail,
                    detail_truncated_count=presentation.detail_truncated_count,
                    duration_ms=activity.duration_ms,
                    error_code=activity.error_code,
                ),
                thread_id=context.thread_id,
                turn_id=context.turn_id,
                operation_id=context.operation_id,
            )

        return asyncio.create_task(
            emit_terminal(),
            name=f"tool-terminal:{request.call_id}",
        )

    def _request_presentation(
        self,
        request: ToolRequest,
        *,
        outcome: str | None = None,
        summary: str = "",
        detail: str | None = None,
    ) -> ToolPresentation:
        registered = self._registry.resolve(request.tool_name)
        configured = (
            registered.spec.display_metadata.get("verb")
            if registered is not None
            else None
        )
        verb = (
            configured
            if isinstance(configured, str) and configured
            else request.tool_name.replace("_", " ").title()
        )
        target_field = {
            "delete": "path",
            "edit_file": "path",
            "execute": "command",
            "glob": "pattern",
            "grep": "pattern",
            "ls": "path",
            "read_file": "path",
            "write_file": "path",
        }.get(request.tool_name)
        candidate = (
            request.arguments.get(target_field) if target_field is not None else None
        )
        target = candidate[:2_000] if isinstance(candidate, str) else None
        return ToolPresentation(
            verb=verb[:64],
            target=target,
            outcome=outcome,
            summary=summary[:2_000],
            detail=detail[:4_000] if detail is not None else None,
        )
