from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from pydantic import JsonValue

from awesome_agent.application.contracts import OperationAccepted
from awesome_agent.application.operations import OperationController
from awesome_agent.conversation import ConversationService, ThreadEntryKind
from awesome_agent.core.cancellation import finish_cancellation_safe
from awesome_agent.core.contracts import new_identifier
from awesome_agent.core.tools import (
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolRequest,
    ToolResult,
)
from awesome_agent.safety import redact_text

_MAX_DIRECT_ENTRY_CHARS = 30_000
logger = logging.getLogger(__name__)
_PRIVATE_PATH = re.compile(
    r"(?i)(?:\b[A-Z]:\\(?:Users|Documents and Settings)\\[^\s\r\n]+|"
    r"(?<![\w.])/(?:Users|home|root|private)/[^\s\r\n]+)"
)


class DirectToolExecutor(Protocol):
    async def execute(
        self,
        request: ToolRequest,
        *,
        context: ToolExecutionContext,
    ) -> ToolResult: ...


type DirectContextFactory = Callable[
    [str, str, ToolRequest], Awaitable[ToolExecutionContext]
]
type DirectOperationFinalizer = Callable[[str], Awaitable[None]]


async def _noop_finalizer(operation_id: str) -> None:
    del operation_id


class DirectCommandService:
    def __init__(
        self,
        *,
        conversation: ConversationService,
        executor: DirectToolExecutor,
        operations: OperationController,
        context_factory: DirectContextFactory,
        finalize_operation: DirectOperationFinalizer = _noop_finalizer,
    ) -> None:
        self._conversation = conversation
        self._executor = executor
        self._operations = operations
        self._context_factory = context_factory
        self._finalize_operation = finalize_operation
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self, thread_id: str, command: str) -> OperationAccepted:
        normalized = command.strip()
        if not normalized:
            raise ValueError("Direct command cannot be empty.")
        reservation = self._operations.reserve()
        try:
            await self._conversation.read_thread(thread_id)
        except BaseException:
            self._operations.abort(reservation)
            raise

        async def execute(operation_id: str) -> None:
            request = ToolRequest(
                call_id=new_identifier("call"),
                tool_name="execute",
                arguments={"command": normalized},
            )
            context = await self._context_factory(thread_id, operation_id, request)
            if (
                context.origin is not ToolExecutionOrigin.DIRECT
                or context.turn_id is not None
                or context.thread_id != thread_id
            ):
                raise RuntimeError("Direct context violates operation authority.")
            try:
                result = await self._executor.execute(request, context=context)
            except asyncio.CancelledError as cancellation:
                await finish_cancellation_safe(
                    self._persist_failure_and_finalize(
                        thread_id,
                        operation_id=operation_id,
                        command=normalized,
                        output="Command was cancelled.",
                        status="cancelled",
                        exit_code=None,
                    )
                )
                raise cancellation
            except Exception:
                await self._operations.commit_failed(
                    operation_id,
                    lambda: self._persist_failure_and_finalize(
                        thread_id,
                        operation_id=operation_id,
                        command=normalized,
                        output="Command execution failed.",
                        status="error",
                        exit_code=None,
                    ),
                )
                raise
            await self._operations.commit_completed(
                operation_id,
                lambda: self._persist_result_and_finalize(
                    thread_id,
                    operation_id,
                    normalized,
                    result,
                ),
            )

        try:
            handle = await self._operations.start_reserved(
                reservation,
                execute,
                thread_id=thread_id,
            )
        except BaseException:
            self._operations.abort(reservation)
            raise
        self._tasks[handle.operation_id] = handle.task
        handle.task.add_done_callback(self._task_completed)
        return OperationAccepted(
            operation_id=handle.operation_id,
            thread_id=thread_id,
        )

    async def wait(self, operation_id: str) -> None:
        task = self._tasks.get(operation_id)
        if task is None:
            raise LookupError(operation_id)
        try:
            await task
        finally:
            self._tasks.pop(operation_id, None)

    def _trim_tasks(self) -> None:
        while len(self._tasks) > 64:
            self._tasks.pop(next(iter(self._tasks)))

    def _task_completed(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()
        self._trim_tasks()

    async def _persist_result_and_finalize(
        self,
        thread_id: str,
        operation_id: str,
        command: str,
        result: ToolResult,
    ) -> None:
        raw_exit = result.metadata.get("exit_code")
        exit_code = raw_exit if isinstance(raw_exit, int) else None
        await self._persist(
            thread_id,
            operation_id=operation_id,
            command=command,
            output=result.content,
            status=result.status.value,
            exit_code=exit_code,
        )
        await self._finalize_operation(operation_id)

    async def _persist_failure_and_finalize(
        self,
        thread_id: str,
        *,
        operation_id: str,
        command: str,
        output: str,
        status: str,
        exit_code: int | None,
    ) -> None:
        try:
            await self._persist(
                thread_id,
                operation_id=operation_id,
                command=command,
                output=output,
                status=status,
                exit_code=exit_code,
            )
        except BaseException:
            logger.warning(
                "Direct terminal transcript persistence failed while preserving "
                "the primary terminal outcome.",
                exc_info=True,
            )
        try:
            await self._finalize_operation(operation_id)
        except BaseException:
            logger.warning(
                "Direct operation finalization failed while preserving the "
                "primary terminal outcome.",
                exc_info=True,
            )

    async def _persist(
        self,
        thread_id: str,
        *,
        operation_id: str,
        command: str,
        output: str,
        status: str,
        exit_code: int | None,
    ) -> None:
        safe_command = _redact_direct_text(command)
        safe_output = _redact_direct_text(output)
        rendered = (
            f"[direct command]\n$ {safe_command}\n"
            f"status: {status}\nexit_status: {exit_code}\n{safe_output}"
        )
        truncated = len(rendered) > _MAX_DIRECT_ENTRY_CHARS
        content = rendered[:_MAX_DIRECT_ENTRY_CHARS]
        metadata = cast(
            dict[str, JsonValue],
            {
                "operation_id": operation_id,
                "exit_code": exit_code,
                "status": status,
                "truncated": truncated,
                "managed_side_effects": False,
            },
        )
        try:
            await self._conversation.append_direct_command(
                thread_id,
                content,
                metadata,
            )
        except (Exception, asyncio.CancelledError):
            if not await self._direct_entry_matches(
                thread_id,
                operation_id=operation_id,
                content=content,
                metadata=metadata,
            ):
                raise
            logger.warning(
                "Direct transcript write raised after its exact durable entry "
                "committed.",
                exc_info=True,
            )

    async def _direct_entry_matches(
        self,
        thread_id: str,
        *,
        operation_id: str,
        content: str,
        metadata: dict[str, JsonValue],
    ) -> bool:
        try:
            view = await self._conversation.read_thread(thread_id)
        except (Exception, asyncio.CancelledError):
            return False
        matches = [
            entry
            for entry in view.entries
            if entry.kind is ThreadEntryKind.DIRECT_COMMAND
            and entry.metadata.get("operation_id") == operation_id
        ]
        return (
            len(matches) == 1
            and matches[0].content == content
            and matches[0].metadata == metadata
        )


def _redact_direct_text(value: str) -> str:
    return _PRIVATE_PATH.sub("[REDACTED:path]", redact_text(value).text)
