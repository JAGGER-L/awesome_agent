from __future__ import annotations

from typing import Protocol

from awesome_agent.application.commands import CommandIntent, CommandResult
from awesome_agent.application.contracts import (
    ApplicationState,
    CancelResult,
    InitializeResult,
    InitializeStatus,
    InteractionResult,
    OperationAccepted,
    ProductError,
    ProductErrorCode,
    ThreadListResult,
    ThreadReadResult,
)


class ApplicationFacade(Protocol):
    async def initialize(self) -> InitializeResult: ...

    async def get_state(self) -> ApplicationState: ...

    async def list_threads(self) -> ThreadListResult: ...

    async def read_thread(self, thread_id: str) -> ThreadReadResult: ...

    async def submit_turn(
        self,
        thread_id: str,
        content: str,
    ) -> OperationAccepted: ...

    async def execute_direct(
        self,
        thread_id: str,
        command: str,
    ) -> OperationAccepted: ...

    async def execute_command(self, intent: CommandIntent) -> CommandResult: ...

    async def respond_interaction(
        self,
        interaction_id: str,
        decision: str,
    ) -> InteractionResult: ...

    async def cancel_operation(self, operation_id: str) -> CancelResult: ...

    async def shutdown(self) -> None: ...


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
        self._initialize_result: InitializeResult | None = None
        self._closed = False

    async def initialize(self) -> InitializeResult:
        if self._initialize_result is None:
            self._initialize_result = await self._backend.initialize_application()
        return self._initialize_result

    async def get_state(self) -> ApplicationState:
        return await self._backend.application_state()

    async def list_threads(self) -> ThreadListResult:
        return await self._backend.workspace_threads()

    async def read_thread(self, thread_id: str) -> ThreadReadResult:
        return await self._backend.thread_state(thread_id)

    async def submit_turn(
        self,
        thread_id: str,
        content: str,
    ) -> OperationAccepted:
        return await self._backend.start_turn(thread_id, content)

    async def execute_direct(
        self,
        thread_id: str,
        command: str,
    ) -> OperationAccepted:
        return await self._backend.start_direct(thread_id, command)

    async def execute_command(self, intent: CommandIntent) -> CommandResult:
        return await self._backend.run_command(intent)

    async def respond_interaction(
        self,
        interaction_id: str,
        decision: str,
    ) -> InteractionResult:
        return await self._backend.resolve_interaction(interaction_id, decision)

    async def cancel_operation(self, operation_id: str) -> CancelResult:
        return await self._backend.cancel_foreground(operation_id)

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._backend.close_application()


__all__ = [
    "ApplicationFacade",
    "ApplicationState",
    "CancelResult",
    "InitializeResult",
    "InitializeStatus",
    "InteractionResult",
    "LocalApplication",
    "OperationAccepted",
    "ProductError",
    "ProductErrorCode",
    "ThreadListResult",
    "ThreadReadResult",
]
