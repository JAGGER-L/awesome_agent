from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import get_type_hints

import pytest
from pydantic import SecretStr

from awesome_agent.application.command_results import (
    CommandOutcome,
    NoticeCommandPayload,
    result,
)
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.contracts import (
    ProviderCredentialSetRequest,
    ProviderCredentialSetResult,
    ProviderCredentialSetStatus,
)
from awesome_agent.application.errors import ApplicationFailure
from awesome_agent.application.facade import (
    ApplicationFacade,
    ApplicationResult,
    ApplicationState,
    CancelResult,
    InitializeResult,
    InitializeStatus,
    InteractionResult,
    LocalApplication,
    OperationAccepted,
    ProductError,
    ProductErrorCode,
    ThreadListQuery,
    ThreadListResult,
    ThreadReadQuery,
    ThreadReadResult,
    WorkspacePresentation,
)
from awesome_agent.application.middleware import (
    ApplicationCall,
    ApplicationInvocation,
    ApplicationObservation,
    ApplicationOperation,
    ObservationalMiddleware,
)
from awesome_agent.config import CredentialSource, SecretStatus
from awesome_agent.modeling import MODEL_CATALOG
from awesome_agent.storage import ApplicationSQLiteUnavailable

METHODS = {
    "initialize",
    "get_state",
    "list_threads",
    "read_thread",
    "submit_turn",
    "execute_direct",
    "execute_command",
    "set_provider_credential",
    "respond_interaction",
    "cancel_operation",
    "shutdown",
}


class Backend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def initialize_application(self) -> InitializeResult:
        self.calls.append(("initialize", None))
        return InitializeResult(
            product_version="0.1.0",
            protocol_version=4,
            status=InitializeStatus.READY,
            session_id="session_1",
            workspace=WorkspacePresentation(display_path="C:\\workspace"),
            capabilities=("turns", "commands"),
        )

    async def application_state(self) -> ApplicationState:
        self.calls.append(("state", None))
        return ApplicationState(
            initialized=True,
            session_id="session_1",
            workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            workspace=WorkspacePresentation(display_path="C:\\workspace"),
            workspace_trusted=True,
            model_catalog=MODEL_CATALOG,
            configuration_valid=True,
            secret_status=SecretStatus(),
        )

    async def workspace_threads(self, query: ThreadListQuery) -> ThreadListResult:
        self.calls.append(("threads", query))
        return ThreadListResult()

    async def thread_state(self, query: ThreadReadQuery) -> ThreadReadResult:
        self.calls.append(("read", query))
        raise LookupError(query.thread_id)

    async def start_turn(
        self,
        thread_id: str,
        content: str,
        client_message_id: str,
    ) -> OperationAccepted:
        self.calls.append(("turn", (thread_id, content, client_message_id)))
        return OperationAccepted(
            operation_id="operation_1",
            thread_id=thread_id,
            turn_id="turn_1",
            client_message_id=client_message_id,
        )

    async def start_direct(self, thread_id: str, command: str) -> OperationAccepted:
        self.calls.append(("direct", (thread_id, command)))
        return OperationAccepted(operation_id="operation_2", thread_id=thread_id)

    async def run_command(self, intent: CommandIntent) -> CommandOutcome:
        self.calls.append(("command", intent))
        return result(NoticeCommandPayload(message="Command completed."))

    async def set_provider_credential(
        self, request: ProviderCredentialSetRequest
    ) -> ProviderCredentialSetResult:
        self.calls.append(("credential", request))
        return ProviderCredentialSetResult(
            provider=request.provider,
            status=ProviderCredentialSetStatus.CONFIGURED,
            source=CredentialSource.AWESOME,
            code="credential_saved",
        )

    async def resolve_interaction(
        self,
        interaction_id: str,
        decision: str,
    ) -> InteractionResult:
        self.calls.append(("interaction", (interaction_id, decision)))
        return InteractionResult(accepted=True, status="resolved")

    async def cancel_foreground(self, operation_id: str) -> CancelResult:
        self.calls.append(("cancel", operation_id))
        return CancelResult(operation_id=operation_id, cancelled=True)

    async def close_application(self) -> None:
        self.calls.append(("shutdown", None))


def _public_async_methods(value: type[object]) -> set[str]:
    return {
        name
        for name, member in value.__dict__.items()
        if not name.startswith("_") and inspect.iscoroutinefunction(member)
    }


def _unwrap[T](result: ApplicationResult[T]) -> T:
    assert result.ok is True
    assert result.error is None
    assert result.value is not None
    return result.value


def test_facade_and_concrete_class_freeze_exact_public_methods() -> None:
    assert _public_async_methods(ApplicationFacade) == METHODS
    assert _public_async_methods(LocalApplication) == METHODS
    assert not METHODS & {"start", "respond", "dispatch", "cancel", "close"}

    annotations = " ".join(
        repr(get_type_hints(member))
        for name, member in ApplicationFacade.__dict__.items()
        if name in METHODS
    )
    for concrete in ("SQLite", "Mcp", "Mem0"):
        assert concrete not in annotations
    assert inspect.iscoroutinefunction(ApplicationFacade.bootstrap_rejection) is False
    assert inspect.iscoroutinefunction(LocalApplication.bootstrap_rejection) is False


@pytest.mark.asyncio
async def test_facade_initialization_and_shutdown_are_idempotent() -> None:
    backend = Backend()
    facade = LocalApplication(backend)

    assert _unwrap(await facade.initialize()) == _unwrap(await facade.initialize())
    assert _unwrap(await facade.shutdown()).stopped is True
    assert _unwrap(await facade.shutdown()).stopped is True

    assert [name for name, _ in backend.calls] == ["initialize", "shutdown"]


@pytest.mark.asyncio
async def test_facade_sets_initializing_before_middleware_and_rolls_back_cancel() -> (
    None
):
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingInitialize:
        async def __call__(
            self,
            invocation: ApplicationInvocation,
            next_call: ApplicationCall,
        ) -> object:
            if invocation.operation is ApplicationOperation.INITIALIZE:
                entered.set()
                await release.wait()
            return await next_call(invocation)

    backend = Backend()
    facade = LocalApplication(backend, middleware=(BlockingInitialize(),))
    initializing = asyncio.create_task(facade.initialize())
    await asyncio.wait_for(entered.wait(), timeout=0.5)

    blocked = facade.bootstrap_rejection(ApplicationOperation.GET_STATE)
    assert blocked is not None
    assert blocked.diagnostic_code == "server_not_ready"
    duplicate = await facade.initialize()
    assert duplicate.ok is False
    assert duplicate.error is not None
    assert duplicate.error.code is ProductErrorCode.OPERATION_BUSY
    assert backend.calls == []

    initializing.cancel("cancel-bootstrap")
    with pytest.raises(asyncio.CancelledError) as cancellation:
        await asyncio.wait_for(initializing, timeout=0.5)
    assert cancellation.value.args == ("cancel-bootstrap",)
    restored = facade.bootstrap_rejection(ApplicationOperation.GET_STATE)
    assert restored is not None
    assert restored.diagnostic_code == "server_not_initialized"


@pytest.mark.asyncio
async def test_malformed_middleware_initialize_result_rolls_back_phase() -> None:
    class MalformedResult:
        async def __call__(
            self,
            invocation: ApplicationInvocation,
            next_call: ApplicationCall,
        ) -> object:
            del next_call
            assert invocation.operation is ApplicationOperation.INITIALIZE
            return object()

    facade = LocalApplication(Backend(), middleware=(MalformedResult(),))

    with pytest.raises(TypeError, match="invalid application result"):
        await facade.initialize()

    rejection = facade.bootstrap_rejection(ApplicationOperation.GET_STATE)
    assert rejection is not None
    assert rejection.diagnostic_code == "server_not_initialized"


@pytest.mark.asyncio
async def test_facade_bootstrap_transition_requires_exact_resolved_trust() -> None:
    class TrustBackend(Backend):
        async def initialize_application(self) -> InitializeResult:
            self.calls.append(("initialize", None))
            return InitializeResult(
                product_version="0.1.0",
                protocol_version=4,
                status=InitializeStatus.TRUST_REQUIRED,
                session_id="session_1",
                interaction_id="interaction_trust",
                workspace=WorkspacePresentation(display_path="C:\\workspace"),
            )

        async def resolve_interaction(
            self,
            interaction_id: str,
            decision: str,
        ) -> InteractionResult:
            self.calls.append(("interaction", (interaction_id, decision)))
            return InteractionResult(accepted=True, status="resolved")

    facade = LocalApplication(TrustBackend())
    initialized = _unwrap(await facade.initialize())
    assert initialized.status is InitializeStatus.TRUST_REQUIRED
    assert facade.bootstrap_rejection(ApplicationOperation.GET_STATE) is not None

    _unwrap(await facade.respond_interaction("interaction_stale", "trust"))
    assert facade.bootstrap_rejection(ApplicationOperation.GET_STATE) is not None

    _unwrap(await facade.respond_interaction("interaction_trust", "trust"))
    assert facade.bootstrap_rejection(ApplicationOperation.GET_STATE) is None


@pytest.mark.asyncio
async def test_facade_maps_application_sqlite_failure_without_leaking_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = Backend()
    private_path = tmp_path / "private" / "application.db"

    async def fail_state() -> ApplicationState:
        raise ApplicationSQLiteUnavailable(private_path)

    monkeypatch.setattr(backend, "application_state", fail_state)
    outcome = await LocalApplication(backend).get_state()

    assert outcome.ok is False
    assert outcome.error is not None
    assert outcome.error.code is ProductErrorCode.STATE_UNAVAILABLE
    assert outcome.error.retryable is True
    assert str(private_path) not in outcome.model_dump_json()


@pytest.mark.asyncio
async def test_facade_delegates_typed_surface_neutral_intents() -> None:
    backend = Backend()
    facade = LocalApplication(backend)
    intent = CommandIntent(name=CommandName.STATUS)

    assert _unwrap(await facade.get_state()).workspace_trusted is True
    assert _unwrap(await facade.list_threads(ThreadListQuery())).threads == ()
    assert (
        _unwrap(
            await facade.submit_turn("thread_1", "inspect", "client_1")
        ).operation_id
        == "operation_1"
    )
    assert (
        _unwrap(await facade.execute_direct("thread_1", "git status")).operation_id
        == "operation_2"
    )
    assert _unwrap(await facade.execute_command(intent)) == result(
        NoticeCommandPayload(message="Command completed.")
    )
    credential = ProviderCredentialSetRequest(
        provider="deepseek",
        action="add",
        api_key=SecretStr("never-render-this"),
    )
    saved = _unwrap(await facade.set_provider_credential(credential))
    assert saved.status is ProviderCredentialSetStatus.CONFIGURED
    assert "never-render-this" not in repr(backend.calls)
    assert (
        _unwrap(await facade.respond_interaction("interaction_1", "trust")).accepted
        is True
    )
    assert _unwrap(await facade.cancel_operation("operation_1")).cancelled is True


@pytest.mark.asyncio
async def test_facade_routes_only_closed_operation_names_through_middleware() -> None:
    backend = Backend()
    invocations: list[ApplicationInvocation] = []

    class Recorder:
        async def __call__(
            self,
            invocation: ApplicationInvocation,
            next_call: ApplicationCall,
        ) -> object:
            invocations.append(invocation)
            return await next_call(invocation)

    facade = LocalApplication(backend, middleware=(Recorder(),))
    await facade.initialize()
    await facade.get_state()
    await facade.list_threads(ThreadListQuery())
    with pytest.raises(LookupError):
        await facade.read_thread(ThreadReadQuery(thread_id="thread_private"))
    await facade.submit_turn(
        "thread_private",
        "private prompt https://private.example",
        "client_private",
    )
    await facade.execute_direct("thread_private", "echo private-secret")
    await facade.execute_command(CommandIntent(name=CommandName.STATUS))
    await facade.set_provider_credential(
        ProviderCredentialSetRequest(
            provider="deepseek",
            action="add",
            api_key=SecretStr("private-credential"),
        )
    )
    await facade.respond_interaction("interaction_private", "trust")
    await facade.cancel_operation("operation_private")
    await facade.shutdown()

    assert [item.operation for item in invocations] == list(ApplicationOperation)
    encoded = "".join(item.model_dump_json() for item in invocations)
    for forbidden in (
        "thread_private",
        "private prompt",
        "private.example",
        "private-secret",
        "private-credential",
        "interaction_private",
        "operation_private",
    ):
        assert forbidden not in encoded


@pytest.mark.asyncio
async def test_facade_observes_mapped_product_error_without_error_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = Backend()
    private_path = tmp_path / "private" / "application.db"
    observations: list[ApplicationObservation] = []

    async def fail_state() -> ApplicationState:
        raise ApplicationSQLiteUnavailable(private_path)

    monkeypatch.setattr(backend, "application_state", fail_state)
    facade = LocalApplication(
        backend,
        middleware=(
            ObservationalMiddleware(
                session_id="session_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                correlation_id=lambda: "correlation_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                clock=lambda: datetime(2026, 7, 27, tzinfo=UTC),
                monotonic=iter((1.0, 1.1)).__next__,
                sink=observations.append,
            ),
        ),
    )

    outcome = await facade.get_state()

    assert outcome.ok is False
    assert observations[0].outcome == "product_error"
    assert observations[0].error_code == ProductErrorCode.STATE_UNAVAILABLE
    assert str(private_path) not in observations[0].model_dump_json()


@pytest.mark.asyncio
async def test_shutdown_observation_precedes_diagnostic_close() -> None:
    backend = Backend()
    order: list[str] = []

    def observe(observation: ApplicationObservation) -> None:
        assert observation.operation is ApplicationOperation.SHUTDOWN
        order.append("observed")

    async def close_diagnostics() -> None:
        order.append("diagnostics_closed")

    facade = LocalApplication(
        backend,
        middleware=(
            ObservationalMiddleware(
                session_id="session_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                correlation_id=lambda: "correlation_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                clock=lambda: datetime(2026, 7, 27, tzinfo=UTC),
                monotonic=iter((1.0, 1.1)).__next__,
                sink=observe,
            ),
        ),
        diagnostics_close=close_diagnostics,
    )

    result = await facade.shutdown()

    assert result.ok is True
    assert order == ["observed", "diagnostics_closed"]


@pytest.mark.asyncio
async def test_internal_diagnostic_close_cancellation_does_not_replace_shutdown() -> (
    None
):
    async def self_cancel_diagnostics() -> None:
        raise asyncio.CancelledError("diagnostics-only")

    facade = LocalApplication(
        Backend(),
        diagnostics_close=self_cancel_diagnostics,
    )

    result = await facade.shutdown()

    assert result.ok is True


@pytest.mark.asyncio
async def test_caller_cancellation_wins_when_diagnostic_close_then_fails() -> None:
    close_started = asyncio.Event()
    fail_close = asyncio.Event()

    async def close_diagnostics() -> None:
        close_started.set()
        await fail_close.wait()
        raise RuntimeError("diagnostics close failed")

    facade = LocalApplication(Backend(), diagnostics_close=close_diagnostics)
    shutdown = asyncio.create_task(facade.shutdown())
    await close_started.wait()
    shutdown.cancel("cancel-shutdown")
    fail_close.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await shutdown
    assert cancelled.value.args == ("cancel-shutdown",)


@pytest.mark.asyncio
async def test_pre_requested_cancellation_wins_over_diagnostic_close_failure() -> None:
    async def close_diagnostics() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("diagnostics close failed")

    def cancel_before_close(observation: ApplicationObservation) -> None:
        if observation.operation is ApplicationOperation.SHUTDOWN:
            current = asyncio.current_task()
            assert current is not None
            current.cancel("pre-requested-shutdown-cancel")

    facade = LocalApplication(
        Backend(),
        middleware=(
            ObservationalMiddleware(
                session_id="session_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                correlation_id=lambda: "correlation_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                monotonic=iter((1.0, 1.1)).__next__,
                sink=cancel_before_close,
            ),
        ),
        diagnostics_close=close_diagnostics,
    )

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await facade.shutdown()
    assert cancelled.value.args == ("pre-requested-shutdown-cancel",)


@pytest.mark.asyncio
async def test_failed_shutdown_keeps_diagnostics_open_for_retry() -> None:
    class RetryBackend(Backend):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        async def close_application(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise ApplicationFailure(
                    ProductError(
                        code=ProductErrorCode.OPERATION_BUSY,
                        message="Shutdown is temporarily unavailable.",
                        retryable=True,
                    )
                )
            await super().close_application()

    diagnostics_closes = 0

    async def close_diagnostics() -> None:
        nonlocal diagnostics_closes
        diagnostics_closes += 1

    backend = RetryBackend()
    facade = LocalApplication(backend, diagnostics_close=close_diagnostics)

    first = await facade.shutdown()
    assert first.ok is False
    assert diagnostics_closes == 0

    second = await facade.shutdown()
    assert second.ok is True
    assert diagnostics_closes == 1
