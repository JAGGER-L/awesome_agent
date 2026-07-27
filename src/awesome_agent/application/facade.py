from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from awesome_agent.application.bootstrap import (
    ApplicationBootstrap,
    BootstrapRejection,
)
from awesome_agent.application.command_results import CommandOutcome
from awesome_agent.application.commands import CommandIntent
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
    ProviderCredentialSetRequest,
    ProviderCredentialSetResult,
    ShutdownResult,
    StatusSnapshot,
    ThreadListQuery,
    ThreadListResult,
    ThreadReadQuery,
    ThreadReadResult,
    WorkspacePresentation,
    thread_display_id,
)
from awesome_agent.application.errors import ApplicationFailure
from awesome_agent.application.middleware import (
    ApplicationInvocation,
    ApplicationMiddleware,
    ApplicationOperation,
    compose_middleware,
)
from awesome_agent.core.cancellation import finish_cancellation_safe
from awesome_agent.storage.compatibility import ApplicationStateUnavailable


class ApplicationFacade(Protocol):
    def bootstrap_rejection(
        self,
        operation: ApplicationOperation | None,
    ) -> BootstrapRejection | None: ...

    async def initialize(self) -> ApplicationResult[InitializeResult]: ...

    async def get_state(self) -> ApplicationResult[ApplicationState]: ...

    async def list_threads(
        self, query: ThreadListQuery
    ) -> ApplicationResult[ThreadListResult]: ...

    async def read_thread(
        self, query: ThreadReadQuery
    ) -> ApplicationResult[ThreadReadResult]: ...

    async def submit_turn(
        self,
        thread_id: str,
        content: str,
        client_message_id: str,
    ) -> ApplicationResult[OperationAccepted]: ...

    async def execute_direct(
        self,
        thread_id: str,
        command: str,
    ) -> ApplicationResult[OperationAccepted]: ...

    async def execute_command(
        self, intent: CommandIntent
    ) -> ApplicationResult[CommandOutcome]: ...

    async def set_provider_credential(
        self, request: ProviderCredentialSetRequest
    ) -> ApplicationResult[ProviderCredentialSetResult]: ...

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

    async def workspace_threads(self, query: ThreadListQuery) -> ThreadListResult: ...

    async def thread_state(self, query: ThreadReadQuery) -> ThreadReadResult: ...

    async def start_turn(
        self,
        thread_id: str,
        content: str,
        client_message_id: str,
    ) -> OperationAccepted: ...

    async def start_direct(
        self,
        thread_id: str,
        command: str,
    ) -> OperationAccepted: ...

    async def run_command(self, intent: CommandIntent) -> CommandOutcome: ...

    async def set_provider_credential(
        self, request: ProviderCredentialSetRequest
    ) -> ProviderCredentialSetResult: ...

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

    def __init__(
        self,
        backend: _ApplicationBackend,
        *,
        middleware: tuple[ApplicationMiddleware, ...] = (),
        diagnostics_close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._backend = backend
        self._middleware = middleware
        self._diagnostics_close = diagnostics_close
        self._bootstrap = ApplicationBootstrap()
        self._initialize_result: ApplicationResult[InitializeResult] | None = None
        self._closed = False

    def bootstrap_rejection(
        self,
        operation: ApplicationOperation | None,
    ) -> BootstrapRejection | None:
        return self._bootstrap.rejection(operation)

    async def initialize(self) -> ApplicationResult[InitializeResult]:
        rejection = self._bootstrap.rejection(ApplicationOperation.INITIALIZE)
        if rejection is not None:
            return ApplicationResult.failure(
                ProductError(
                    code=ProductErrorCode.OPERATION_BUSY,
                    message="Application initialization is in progress.",
                    retryable=True,
                )
            )
        transition = self._bootstrap.begin_initialize()
        try:
            result = await self._invoke(
                ApplicationOperation.INITIALIZE,
                self._initialize,
            )
        except BaseException:
            self._bootstrap.abort_initialize(transition)
            raise
        self._bootstrap.complete_initialize(transition, result)
        return result

    async def _initialize(self) -> ApplicationResult[InitializeResult]:
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
        return await self._invoke(
            ApplicationOperation.GET_STATE,
            lambda: self._call(self._backend.application_state),
        )

    async def list_threads(
        self, query: ThreadListQuery
    ) -> ApplicationResult[ThreadListResult]:
        return await self._invoke(
            ApplicationOperation.LIST_THREADS,
            lambda: self._call(lambda: self._backend.workspace_threads(query)),
        )

    async def read_thread(
        self, query: ThreadReadQuery
    ) -> ApplicationResult[ThreadReadResult]:
        return await self._invoke(
            ApplicationOperation.READ_THREAD,
            lambda: self._call(lambda: self._backend.thread_state(query)),
        )

    async def submit_turn(
        self,
        thread_id: str,
        content: str,
        client_message_id: str,
    ) -> ApplicationResult[OperationAccepted]:
        return await self._invoke(
            ApplicationOperation.SUBMIT_TURN,
            lambda: self._call(
                lambda: self._backend.start_turn(
                    thread_id,
                    content,
                    client_message_id,
                )
            ),
        )

    async def execute_direct(
        self,
        thread_id: str,
        command: str,
    ) -> ApplicationResult[OperationAccepted]:
        return await self._invoke(
            ApplicationOperation.EXECUTE_DIRECT,
            lambda: self._call(lambda: self._backend.start_direct(thread_id, command)),
        )

    async def execute_command(
        self, intent: CommandIntent
    ) -> ApplicationResult[CommandOutcome]:
        return await self._invoke(
            ApplicationOperation.EXECUTE_COMMAND,
            lambda: self._call(lambda: self._backend.run_command(intent)),
        )

    async def set_provider_credential(
        self, request: ProviderCredentialSetRequest
    ) -> ApplicationResult[ProviderCredentialSetResult]:
        return await self._invoke(
            ApplicationOperation.SET_PROVIDER_CREDENTIAL,
            lambda: self._call(lambda: self._backend.set_provider_credential(request)),
        )

    async def respond_interaction(
        self,
        interaction_id: str,
        decision: str,
    ) -> ApplicationResult[InteractionResult]:
        transition = self._bootstrap.begin_interaction(interaction_id, decision)
        result = await self._invoke(
            ApplicationOperation.RESPOND_INTERACTION,
            lambda: self._call(
                lambda: self._backend.resolve_interaction(interaction_id, decision)
            ),
        )
        if transition is not None:
            self._bootstrap.complete_interaction(transition, result)
        return result

    async def cancel_operation(
        self, operation_id: str
    ) -> ApplicationResult[CancelResult]:
        return await self._invoke(
            ApplicationOperation.CANCEL_OPERATION,
            lambda: self._call(lambda: self._backend.cancel_foreground(operation_id)),
        )

    async def shutdown(self) -> ApplicationResult[ShutdownResult]:
        if self._closed:
            return ApplicationResult.success(ShutdownResult())
        try:
            result = await self._invoke(
                ApplicationOperation.SHUTDOWN,
                lambda: self._call(self._close_backend),
            )
        except BaseException as error:
            await self._close_diagnostics(primary=error)
            raise
        if result.ok:
            self._closed = True
            await self._close_diagnostics()
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
        except ApplicationStateUnavailable:
            return ApplicationResult.failure(
                ProductError(
                    code=ProductErrorCode.STATE_UNAVAILABLE,
                    message="Application state cannot be accessed safely.",
                    retryable=True,
                )
            )

    async def _invoke[T](
        self,
        operation: ApplicationOperation,
        call: Callable[[], Awaitable[ApplicationResult[T]]],
    ) -> ApplicationResult[T]:
        async def terminal(_invocation: ApplicationInvocation) -> object:
            return await call()

        observed = await compose_middleware(self._middleware, terminal)(
            ApplicationInvocation(operation=operation)
        )
        return cast(ApplicationResult[T], observed)

    async def _close_diagnostics(
        self,
        *,
        primary: BaseException | None = None,
    ) -> None:
        close = self._diagnostics_close
        self._diagnostics_close = None
        if close is None:
            return
        current = asyncio.current_task()
        try:
            _, cancellation = await finish_cancellation_safe(close())
        except asyncio.CancelledError:
            if primary is None and current is not None and current.cancelling() > 0:
                raise
            return
        except BaseException:
            return
        if primary is None and cancellation is not None:
            raise cancellation


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
    "ProviderCredentialSetRequest",
    "ProviderCredentialSetResult",
    "ShutdownResult",
    "StatusSnapshot",
    "ThreadListQuery",
    "ThreadListResult",
    "ThreadReadQuery",
    "ThreadReadResult",
    "WorkspacePresentation",
    "thread_display_id",
]
