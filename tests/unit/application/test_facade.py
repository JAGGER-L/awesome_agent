from __future__ import annotations

import inspect
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
    ThreadListQuery,
    ThreadListResult,
    ThreadReadQuery,
    ThreadReadResult,
    WorkspacePresentation,
)
from awesome_agent.config import CredentialSource, SecretStatus

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
            protocol_version=2,
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


@pytest.mark.asyncio
async def test_facade_initialization_and_shutdown_are_idempotent() -> None:
    backend = Backend()
    facade = LocalApplication(backend)

    assert _unwrap(await facade.initialize()) == _unwrap(await facade.initialize())
    assert _unwrap(await facade.shutdown()).stopped is True
    assert _unwrap(await facade.shutdown()).stopped is True

    assert [name for name, _ in backend.calls] == ["initialize", "shutdown"]


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
