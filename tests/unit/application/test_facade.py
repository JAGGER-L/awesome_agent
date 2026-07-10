from __future__ import annotations

import inspect
from typing import get_type_hints

import pytest

from awesome_agent.application.commands import (
    CommandIntent,
    CommandName,
    CommandResult,
    CommandStatus,
)
from awesome_agent.application.facade import (
    ApplicationFacade,
    ApplicationState,
    CancelResult,
    InitializeResult,
    InitializeStatus,
    InteractionResult,
    LocalApplication,
    OperationAccepted,
    ThreadListResult,
)
from awesome_agent.config import SecretStatus

METHODS = {
    "initialize",
    "get_state",
    "list_threads",
    "read_thread",
    "submit_turn",
    "execute_direct",
    "execute_command",
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
            status=InitializeStatus.READY,
            session_id="session_1",
            capabilities=("turns", "commands"),
        )

    async def application_state(self) -> ApplicationState:
        self.calls.append(("state", None))
        return ApplicationState(
            initialized=True,
            session_id="session_1",
            workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            workspace_trusted=True,
            configuration_valid=True,
            secret_status=SecretStatus(),
        )

    async def workspace_threads(self) -> ThreadListResult:
        self.calls.append(("threads", None))
        return ThreadListResult()

    async def thread_state(self, thread_id: str) -> object:
        self.calls.append(("read", thread_id))
        raise LookupError(thread_id)

    async def start_turn(self, thread_id: str, content: str) -> OperationAccepted:
        self.calls.append(("turn", (thread_id, content)))
        return OperationAccepted(operation_id="operation_1", thread_id=thread_id)

    async def start_direct(self, thread_id: str, command: str) -> OperationAccepted:
        self.calls.append(("direct", (thread_id, command)))
        return OperationAccepted(operation_id="operation_2", thread_id=thread_id)

    async def run_command(self, intent: CommandIntent) -> CommandResult:
        self.calls.append(("command", intent))
        return CommandResult(status=CommandStatus.SUCCESS)

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


def test_facade_and_concrete_class_freeze_exact_ten_methods() -> None:
    assert _public_async_methods(ApplicationFacade) == METHODS
    assert _public_async_methods(LocalApplication) == METHODS
    assert not METHODS & {"start", "respond", "dispatch", "cancel", "close"}

    annotations = " ".join(
        repr(get_type_hints(member))
        for name, member in ApplicationFacade.__dict__.items()
        if name in METHODS
    )
    for concrete in ("Provider", "SQLite", "Mcp", "Mem0"):
        assert concrete not in annotations


@pytest.mark.asyncio
async def test_facade_initialization_and_shutdown_are_idempotent() -> None:
    backend = Backend()
    facade = LocalApplication(backend)

    assert await facade.initialize() == await facade.initialize()
    await facade.shutdown()
    await facade.shutdown()

    assert [name for name, _ in backend.calls] == ["initialize", "shutdown"]


@pytest.mark.asyncio
async def test_facade_delegates_typed_surface_neutral_intents() -> None:
    backend = Backend()
    facade = LocalApplication(backend)
    intent = CommandIntent(name=CommandName.STATUS)

    assert (await facade.get_state()).workspace_trusted is True
    assert (await facade.list_threads()).threads == ()
    assert (await facade.submit_turn("thread_1", "inspect")).operation_id == (
        "operation_1"
    )
    assert (await facade.execute_direct("thread_1", "git status")).operation_id == (
        "operation_2"
    )
    assert (await facade.execute_command(intent)).status is CommandStatus.SUCCESS
    assert (await facade.respond_interaction("interaction_1", "trust")).accepted is True
    assert (await facade.cancel_operation("operation_1")).cancelled is True
