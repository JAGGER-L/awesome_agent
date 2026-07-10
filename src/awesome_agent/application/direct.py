from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Protocol, cast

from pydantic import JsonValue

from awesome_agent.application.contracts import OperationAccepted
from awesome_agent.application.operations import OperationController
from awesome_agent.conversation import ConversationService
from awesome_agent.core.contracts import new_identifier
from awesome_agent.core.tools import (
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolRequest,
    ToolResult,
)
from awesome_agent.safety import redact_text

_MAX_DIRECT_ENTRY_CHARS = 30_000
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


type DirectContextFactory = Callable[[str, str], ToolExecutionContext]


class DirectCommandService:
    def __init__(
        self,
        *,
        conversation: ConversationService,
        executor: DirectToolExecutor,
        operations: OperationController,
        context_factory: DirectContextFactory,
    ) -> None:
        self._conversation = conversation
        self._executor = executor
        self._operations = operations
        self._context_factory = context_factory
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self, thread_id: str, command: str) -> OperationAccepted:
        normalized = command.strip()
        if not normalized:
            raise ValueError("Direct command cannot be empty.")
        self._conversation.read_thread(thread_id)

        async def execute(operation_id: str) -> None:
            context = self._context_factory(thread_id, operation_id)
            if (
                context.origin is not ToolExecutionOrigin.DIRECT
                or context.turn_id is not None
                or context.thread_id != thread_id
            ):
                raise RuntimeError("Direct context violates operation authority.")
            request = ToolRequest(
                call_id=new_identifier("call"),
                tool_name="execute",
                arguments={"command": normalized},
            )
            try:
                result = await self._executor.execute(request, context=context)
            except asyncio.CancelledError:
                self._persist(
                    thread_id,
                    command=normalized,
                    output="Command was cancelled.",
                    status="cancelled",
                    exit_code=None,
                )
                raise
            except Exception:
                self._persist(
                    thread_id,
                    command=normalized,
                    output="Command execution failed.",
                    status="error",
                    exit_code=None,
                )
                raise
            self._persist_result(thread_id, normalized, result)

        handle = await self._operations.start(execute, thread_id=thread_id)
        self._tasks[handle.operation_id] = handle.task
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

    def _persist_result(
        self,
        thread_id: str,
        command: str,
        result: ToolResult,
    ) -> None:
        raw_exit = result.metadata.get("exit_code")
        exit_code = raw_exit if isinstance(raw_exit, int) else None
        self._persist(
            thread_id,
            command=command,
            output=result.content,
            status=result.status.value,
            exit_code=exit_code,
        )

    def _persist(
        self,
        thread_id: str,
        *,
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
        self._conversation.append_direct_command(
            thread_id,
            rendered[:_MAX_DIRECT_ENTRY_CHARS],
            cast(
                dict[str, JsonValue],
                {
                    "exit_code": exit_code,
                    "status": status,
                    "truncated": truncated,
                    "managed_side_effects": False,
                },
            ),
        )


def _redact_direct_text(value: str) -> str:
    return _PRIVATE_PATH.sub("[REDACTED:path]", redact_text(value).text)
