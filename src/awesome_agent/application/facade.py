from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from awesome_agent.application.commands import CommandIntent, CommandResult
from awesome_agent.application.contracts import (
    ApplicationResult,
    ApplicationState,
    CancelResult,
    InitializeResult,
    InitializeStatus,
    InteractionResult,
    OperationAccepted,
    ProductError,
    ProductErrorCode,
    ShutdownResult,
    ThreadListResult,
    ThreadReadResult,
)
from awesome_agent.application.errors import ApplicationFailure


class ApplicationFacade(Protocol):
    async def initialize(self) -> ApplicationResult[InitializeResult]: ...

    async def get_state(self) -> ApplicationResult[ApplicationState]: ...

    async def list_threads(self) -> ApplicationResult[ThreadListResult]: ...

    async def read_thread(
        self, thread_id: str
    ) -> ApplicationResult[ThreadReadResult]: ...

    async def submit_turn(
        self,
        thread_id: str,
        content: str,
    ) -> ApplicationResult[OperationAccepted]: ...

    async def execute_direct(
        self,
        thread_id: str,
        command: str,
    ) -> ApplicationResult[OperationAccepted]: ...

    async def execute_command(
        self, intent: CommandIntent
    ) -> ApplicationResult[CommandResult]: ...

    async def respond_interaction(
        self,
        interaction_id: str,
        decision: str,
    ) -> ApplicationResult[InteractionResult]: ...

    async def cancel_operation(
        self, operation_id: str
    ) -> ApplicationResult[CancelResult]: ...

    async def shutdown(self) -> ApplicationResult[ShutdownResult]: ...


class _ApplicationBackend(Protocol):
    async def initialize_application(self) -> InitializeResult: ...

    async def application_state(self) -> ApplicationState: ...

    async def workspace_threads(self) -> ThreadListResult: ...

    async def thread_state(self, thread_id: str) -> ThreadReadResult: ...

    async def start_turn(
        self,
        thread_id: str,
        content: str,
    ) -> OperationAccepted: ...

    async def start_direct(
        self,
        thread_id: str,
        command: str,
    ) -> OperationAccepted: ...

    async def run_command(self, intent: CommandIntent) -> CommandResult: ...

    async def resolve_interaction(
        self,
        interaction_id: str,
        decision: str,
    ) -> InteractionResult: ...

    async def cancel_foreground(self, operation_id: str) -> CancelResult: ...

    async def close_application(self) -> None: ...


class LocalApplication:
    """The only surface-facing product API.

    Concrete adapters and repositories remain behind the injected application backend.
    """

    def __init__(self, backend: _ApplicationBackend) -> None:
        self._backend = backend
        self._initialize_result: ApplicationResult[InitializeResult] | None = None
        self._closed = False

    async def initialize(self) -> ApplicationResult[InitializeResult]:
        if (
            self._initialize_result is None
            or not self._initialize_result.ok
            or self._initialize_result.value is None
            or self._initialize_result.value.status is not InitializeStatus.READY
        ):
            self._initialize_result = await self._call(
                self._backend.initialize_application
            )
        return self._initialize_result

    async def get_state(self) -> ApplicationResult[ApplicationState]:
        return await self._call(self._backend.application_state)

    async def list_threads(self) -> ApplicationResult[ThreadListResult]:
        return await self._call(self._backend.workspace_threads)

    async def read_thread(self, thread_id: str) -> ApplicationResult[ThreadReadResult]:
        return await self._call(lambda: self._backend.thread_state(thread_id))

    async def submit_turn(
        self,
        thread_id: str,
        content: str,
    ) -> ApplicationResult[OperationAccepted]:
        return await self._call(lambda: self._backend.start_turn(thread_id, content))

    async def execute_direct(
        self,
        thread_id: str,
        command: str,
    ) -> ApplicationResult[OperationAccepted]:
        return await self._call(lambda: self._backend.start_direct(thread_id, command))

    async def execute_command(
        self, intent: CommandIntent
    ) -> ApplicationResult[CommandResult]:
        return await self._call(lambda: self._backend.run_command(intent))

    async def respond_interaction(
        self,
        interaction_id: str,
        decision: str,
    ) -> ApplicationResult[InteractionResult]:
        return await self._call(
            lambda: self._backend.resolve_interaction(interaction_id, decision)
        )

    async def cancel_operation(
        self, operation_id: str
    ) -> ApplicationResult[CancelResult]:
        return await self._call(lambda: self._backend.cancel_foreground(operation_id))

    async def shutdown(self) -> ApplicationResult[ShutdownResult]:
        if self._closed:
            return ApplicationResult.success(ShutdownResult())
        result = await self._call(self._close_backend)
        if result.ok:
            self._closed = True
        return result

    async def _close_backend(self) -> ShutdownResult:
        await self._backend.close_application()
        return ShutdownResult()

    async def _call[T](
        self,
        call: Callable[[], Awaitable[T]],
    ) -> ApplicationResult[T]:
        try:
            return ApplicationResult.success(await call())
        except ApplicationFailure as failure:
            return ApplicationResult.failure(failure.error)


__all__ = [
    "ApplicationFacade",
    "ApplicationResult",
    "ApplicationState",
    "CancelResult",
    "InitializeResult",
    "InitializeStatus",
    "InteractionResult",
    "LocalApplication",
    "OperationAccepted",
    "ProductError",
    "ProductErrorCode",
    "ShutdownResult",
    "ThreadListResult",
    "ThreadReadResult",
]
