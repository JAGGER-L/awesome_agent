from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from typing import Literal, cast

from pydantic import BaseModel, JsonValue, ValidationError

from awesome_agent.core.cancellation import finish_cancellation_safe
from awesome_agent.core.citations import Citation
from awesome_agent.core.events import EventType, ToolResultPayload, ToolStartedPayload
from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import (
    ToolActivityDraft,
    ToolError,
    ToolErrorCode,
    ToolInvocationDescription,
    ToolOutput,
    ToolPresentation,
    ToolRequest,
    ToolResult,
    ToolStatus,
)
from awesome_agent.core.tools.errors import (
    ExpectedToolFailure,
    ToolInvariantError,
    validate_expected_tool_failure,
)
from awesome_agent.core.tools.permissions import (
    PermissionPolicy,
    PolicyAction,
    PolicyDecision,
    PolicyRequest,
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolCapability,
)
from awesome_agent.core.tools.registry import RegisteredTool, ToolRegistry

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
        presentation = self._static_presentation(request, registered)
        if registered is None:
            await self._emit_started_with_cancellation(
                request,
                context,
                started,
                presentation,
            )
            return await self._error_result(
                request,
                context,
                started,
                ToolErrorCode.NOT_FOUND,
                "Unknown tool.",
                presentation=presentation,
            )

        try:
            arguments = registered.input_model.model_validate(request.arguments)
        except asyncio.CancelledError as cancellation:
            await finish_cancellation_safe(
                self._emit_started(request, context, presentation)
            )
            await self._finalize_cancellation(
                request,
                context,
                started,
                presentation=presentation,
            )
            raise cancellation
        except ValidationError:
            await self._emit_started_with_cancellation(
                request,
                context,
                started,
                presentation,
            )
            return await self._error_result(
                request,
                context,
                started,
                ToolErrorCode.INVALID_ARGUMENTS,
                "Tool arguments did not match the schema.",
                presentation=presentation,
            )
        except Exception as error:
            await self._emit_started_with_cancellation(
                request,
                context,
                started,
                presentation,
            )
            await self._finalize_unexpected_failure(
                request,
                context,
                started,
                presentation=presentation,
            )
            raise ToolInvariantError(
                "Unexpected tool input validation failure."
            ) from error

        try:
            admit = cast(
                Callable[[BaseModel, ToolExecutionContext], object],
                registered.admit,
            )
            admission_result = admit(arguments, context)
            if admission_result is not None:
                self._close_awaitable(admission_result)
                raise TypeError("Tool admitter returned an invalid contract")
            description_result: object = registered.describe(arguments)
            if not isinstance(description_result, ToolInvocationDescription):
                self._close_awaitable(description_result)
                raise TypeError("Tool describer returned an invalid contract")
            description = ToolInvocationDescription.model_validate(
                {
                    "verb": description_result.verb,
                    "display_target": description_result.display_target,
                    "approval_operation": description_result.approval_operation,
                    "approval_target": description_result.approval_target,
                },
                strict=True,
            )
        except asyncio.CancelledError as cancellation:
            await finish_cancellation_safe(
                self._emit_started(request, context, presentation)
            )
            await self._finalize_cancellation(
                request,
                context,
                started,
                presentation=presentation,
            )
            raise cancellation
        except ExpectedToolFailure as error:
            try:
                failure = validate_expected_tool_failure(error)
            except TypeError as contract_error:
                await self._emit_started_with_cancellation(
                    request,
                    context,
                    started,
                    presentation,
                )
                await self._finalize_unexpected_failure(
                    request,
                    context,
                    started,
                    presentation=presentation,
                )
                raise ToolInvariantError(
                    "Unexpected tool invocation policy failure."
                ) from contract_error
            await self._emit_started_with_cancellation(
                request,
                context,
                started,
                presentation,
            )
            return await self._error_result(
                request,
                context,
                started,
                failure.code,
                failure.message,
                presentation=presentation,
                retryable=failure.retryable,
                metadata=failure.metadata,
            )
        except Exception as error:
            await self._emit_started_with_cancellation(
                request,
                context,
                started,
                presentation,
            )
            await self._finalize_unexpected_failure(
                request,
                context,
                started,
                presentation=presentation,
            )
            raise ToolInvariantError(
                "Unexpected tool invocation policy failure."
            ) from error

        presentation = ToolPresentation(
            verb=description.verb,
            target=description.display_target,
        )
        await self._emit_started_with_cancellation(
            request,
            context,
            started,
            presentation,
        )
        try:
            decision_result: object = self._permission_policy.evaluate(
                PolicyRequest(
                    capability=registered.spec.capability,
                    mode=context.permission_session.mode,
                    granted_capabilities=frozenset(
                        context.permission_session.granted_capabilities
                    ),
                )
            )
            if (
                not isinstance(decision_result, PolicyDecision)
                or not isinstance(decision_result.action, PolicyAction)
                or not isinstance(decision_result.reason, str)
                or not decision_result.reason
                or len(decision_result.reason) > 2_000
            ):
                raise ToolInvariantError(
                    "Permission policy returned an invalid decision."
                )
            decision = decision_result
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
                approval_result: object = await resolver(
                    self._approval_request(
                        registered.spec.capability,
                        description,
                    )
                )
                if not isinstance(approval_result, ToolApprovalDecision):
                    raise ToolInvariantError(
                        "Approval resolver returned an invalid decision."
                    )
                approval = approval_result
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
                elif approval is not ToolApprovalDecision.ALLOW_ONCE:
                    raise ToolInvariantError(
                        "Approval resolver returned an invalid decision."
                    )
            elif decision.action is not PolicyAction.ALLOW:
                raise ToolInvariantError(
                    "Permission policy returned an invalid decision."
                )
            timeout_result: object = (
                registered.timeout_resolver(arguments)
                if registered.timeout_resolver is not None
                else self._timeout_seconds
            )
            if (
                isinstance(timeout_result, bool)
                or not isinstance(timeout_result, (int, float))
                or timeout_result <= 0
                or not math.isfinite(timeout_result)
            ):
                self._close_awaitable(timeout_result)
                raise ToolInvariantError("Tool timeout must be positive.")
            total_timeout = float(timeout_result)
            output_result: object = await self._invoke_with_deadline(
                registered,
                arguments,
                context,
                timeout_seconds=total_timeout,
            )
            if not isinstance(output_result, ToolOutput):
                self._close_awaitable(output_result)
                raise ToolInvariantError(
                    "Tool handler returned an invalid output contract."
                )
            output_presentation = output_result.presentation
            if output_presentation is not None:
                if not isinstance(output_presentation, ToolPresentation):
                    self._close_awaitable(output_presentation)
                    raise ToolInvariantError(
                        "Tool handler returned an invalid presentation contract."
                    )
                output_presentation = ToolPresentation.model_validate(
                    {
                        "verb": output_presentation.verb,
                        "target": output_presentation.target,
                        "outcome": output_presentation.outcome,
                        "summary": output_presentation.summary,
                        "detail": output_presentation.detail,
                        "detail_truncated_count": (
                            output_presentation.detail_truncated_count
                        ),
                        "duration_ms": output_presentation.duration_ms,
                    },
                    strict=True,
                )
            output_citations = output_result.citations
            if not isinstance(output_citations, tuple):
                self._close_awaitable(output_citations)
                raise ToolInvariantError(
                    "Tool handler returned an invalid citation contract."
                )
            citations: list[Citation] = []
            for citation in output_citations:
                if not isinstance(citation, Citation):
                    self._close_awaitable(citation)
                    raise ToolInvariantError(
                        "Tool handler returned an invalid citation contract."
                    )
                citations.append(
                    Citation.model_validate(
                        {
                            "id": citation.id,
                            "title": citation.title,
                            "url": citation.url,
                        },
                        strict=True,
                    )
                )
            output = ToolOutput.model_validate(
                {
                    "content": output_result.content,
                    "metadata": output_result.metadata,
                    "presentation": output_presentation,
                    "citations": tuple(citations),
                },
                strict=True,
            )
        except asyncio.CancelledError as cancellation:
            await self._finalize_cancellation(
                request,
                context,
                started,
                presentation=presentation,
            )
            raise cancellation
        except TimeoutError:
            return await self._error_result(
                request,
                context,
                started,
                ToolErrorCode.TIMEOUT,
                "Tool execution timed out.",
                presentation=presentation,
            )
        except ExpectedToolFailure as error:
            try:
                failure = validate_expected_tool_failure(error)
            except TypeError as contract_error:
                await self._finalize_unexpected_failure(
                    request,
                    context,
                    started,
                    presentation=presentation,
                )
                raise ToolInvariantError(
                    "Unexpected tool handler failure."
                ) from contract_error
            return await self._error_result(
                request,
                context,
                started,
                failure.code,
                failure.message,
                presentation=presentation,
                retryable=failure.retryable,
                metadata=failure.metadata,
            )
        except Exception as error:
            await self._finalize_unexpected_failure(
                request,
                context,
                started,
                presentation=presentation,
            )
            raise ToolInvariantError("Unexpected tool handler failure.") from error

        presentation = output.presentation or presentation.model_copy(
            update={
                "outcome": "Completed",
                "summary": "Completed",
                "detail": output.content[:4_000] or None,
            }
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
            citations=output.citations,
        )
        return result

    @staticmethod
    def _static_presentation(
        request: ToolRequest,
        registered: RegisteredTool | None,
    ) -> ToolPresentation:
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
        return ToolPresentation(verb=verb[:64])

    @staticmethod
    async def _emit_started(
        request: ToolRequest,
        context: ToolExecutionContext,
        presentation: ToolPresentation,
    ) -> None:
        await context.emitter.emit(
            ToolStartedPayload(
                call_id=request.call_id,
                tool_name=request.tool_name,
                verb=presentation.verb,
                target=presentation.target,
            ),
            thread_id=context.thread_id,
            turn_id=context.turn_id,
            operation_id=context.operation_id,
        )

    async def _emit_started_with_cancellation(
        self,
        request: ToolRequest,
        context: ToolExecutionContext,
        started: float,
        presentation: ToolPresentation,
    ) -> None:
        try:
            await self._emit_started(request, context, presentation)
        except asyncio.CancelledError as cancellation:
            await self._finalize_cancellation(
                request,
                context,
                started,
                presentation=presentation,
            )
            raise cancellation

    @staticmethod
    def _close_awaitable(value: object) -> None:
        close = getattr(value, "close", None)
        if callable(close):
            try:
                close()
            except BaseException:
                logger.warning(
                    "Invalid tool callback awaitable could not be closed.",
                    exc_info=True,
                )
        elif isinstance(value, asyncio.Future):
            value.cancel()

    async def _finalize_cancellation(
        self,
        request: ToolRequest,
        context: ToolExecutionContext,
        started: float,
        *,
        presentation: ToolPresentation,
    ) -> None:
        measured, activity = self._terminal_artifacts(
            request,
            context,
            started,
            outcome="cancelled",
            presentation=presentation.model_copy(
                update={"outcome": "Cancelled", "summary": "Cancelled"}
            ),
            error_code=ToolErrorCode.CANCELLED.value,
        )
        await finish_cancellation_safe(
            self._persist_activity_after_cancellation(context, activity)
        )
        terminal_task = self._start_terminal_event(
            request,
            context,
            measured,
            activity,
            event_type=EventType.TOOL_CANCELLED,
        )
        await self._finish_terminal_event(terminal_task)

    async def _finalize_unexpected_failure(
        self,
        request: ToolRequest,
        context: ToolExecutionContext,
        started: float,
        *,
        presentation: ToolPresentation,
    ) -> None:
        await self._finalize_handler_outcome(
            request,
            context,
            started,
            outcome="error",
            presentation=presentation.model_copy(
                update={
                    "outcome": "Failed",
                    "summary": ToolErrorCode.EXECUTION_FAILED.value,
                    "detail": "Tool execution failed.",
                }
            ),
            error_code=ToolErrorCode.EXECUTION_FAILED.value,
            event_type=EventType.TOOL_FAILED,
        )

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
    def _approval_request(
        capability: str,
        description: ToolInvocationDescription,
    ) -> ToolApprovalRequest:
        operation = description.approval_operation
        target = description.approval_target
        full_prompt = f"Do you want to {operation} {target}?"
        prompt = (
            full_prompt if len(full_prompt) <= 2_000 else f"{full_prompt[:1_999]}\u2026"
        )
        return ToolApprovalRequest(
            capability=capability,
            operation=operation,
            target=target,
            prompt=prompt,
        )

    async def _error_result(
        self,
        request: ToolRequest,
        context: ToolExecutionContext,
        started: float,
        code: ToolErrorCode,
        message: str,
        *,
        presentation: ToolPresentation,
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
            presentation=presentation.model_copy(
                update={
                    "outcome": "Failed",
                    "summary": code.value,
                    "detail": bounded,
                }
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
