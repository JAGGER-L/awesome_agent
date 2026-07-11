from __future__ import annotations

import inspect
from pathlib import Path
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
    WorkspacePresentation,
)
from awesome_agent.application.headless import ConversationCommandService
from awesome_agent.config import SecretStatus
from awesome_agent.conversation import ConversationService
from awesome_agent.storage.conversations import SQLiteConversationRepositories

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
            product_version="0.1.0",
            protocol_version=1,
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

    async def thread_state(self, query: ThreadReadQuery) -> object:
        self.calls.append(("read", query))
        raise LookupError(query.thread_id)

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


def _unwrap[T](result: ApplicationResult[T]) -> T:
    assert result.ok is True
    assert result.error is None
    assert result.value is not None
    return result.value


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
    assert _unwrap(await facade.submit_turn("thread_1", "inspect")).operation_id == (
        "operation_1"
    )
    assert (
        _unwrap(await facade.execute_direct("thread_1", "git status")).operation_id
        == "operation_2"
    )
    assert _unwrap(await facade.execute_command(intent)).status is CommandStatus.SUCCESS
    assert (
        _unwrap(await facade.respond_interaction("interaction_1", "trust")).accepted
        is True
    )
    assert _unwrap(await facade.cancel_operation("operation_1")).cancelled is True


@pytest.mark.asyncio
async def test_conversation_commands_select_future_thread_configuration(
    tmp_path: Path,
) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    delegated: list[tuple[CommandName, str]] = []

    async def delegate(intent: CommandIntent, thread_id: str) -> CommandResult:
        delegated.append((intent.name, thread_id))
        return CommandResult(status=CommandStatus.SUCCESS)

    commands = ConversationCommandService(
        conversation=conversation,
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        delegate=delegate,
    )

    created = await commands.handle(
        CommandIntent(name=CommandName.NEW, arguments=("Feature", "work"))
    )
    thread_id = created.data["thread_id"]
    assert created.data["title"] == "Feature work"

    model_query = await commands.handle(CommandIntent(name=CommandName.MODEL))
    thinking_query = await commands.handle(CommandIntent(name=CommandName.THINKING))
    assert model_query.selection is not None
    assert thinking_query.data["thinking_enabled"] is False
    assert thinking_query.selection is not None

    model = await commands.handle(
        CommandIntent(
            name=CommandName.MODEL,
            arguments=("kimi/kimi-k2.6",),
        )
    )
    thinking = await commands.handle(
        CommandIntent(name=CommandName.THINKING, arguments=("on",))
    )
    selected = conversation.read_thread(str(thread_id)).thread
    assert model.data["model"] == "kimi/kimi-k2.6"
    assert thinking.data["thinking_enabled"] is True
    assert selected.current_model == "kimi/kimi-k2.6"
    assert selected.thinking_enabled is True

    delegated_result = await commands.handle(CommandIntent(name=CommandName.STATUS))
    assert delegated_result.status is CommandStatus.SUCCESS
    assert delegated == [(CommandName.STATUS, thread_id)]


@pytest.mark.asyncio
async def test_resume_is_workspace_scoped_and_ink_commands_are_surface_owned(
    tmp_path: Path,
) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    own = conversation.create_thread("ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    conversation.create_thread("ws_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")

    async def delegate(intent: CommandIntent, thread_id: str) -> CommandResult:
        del intent, thread_id
        return CommandResult(status=CommandStatus.SUCCESS)

    commands = ConversationCommandService(
        conversation=conversation,
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        delegate=delegate,
    )

    picker = await commands.handle(CommandIntent(name=CommandName.RESUME))
    selected = await commands.handle(
        CommandIntent(name=CommandName.RESUME, arguments=(own.id,))
    )
    surface = await commands.handle(CommandIntent(name=CommandName.HELP))
    invalid = await commands.handle(
        CommandIntent(name=CommandName.RESUME, arguments=("foreign",))
    )

    assert picker.selection is not None
    assert [option.value for option in picker.selection.options] == [own.id]
    assert selected.data["thread_id"] == own.id
    assert surface.data["error_code"] == "surface_command"
    assert invalid.data["error_code"] == "thread_not_found"


@pytest.mark.asyncio
async def test_resume_accepts_full_or_unique_prefix_and_selects_ambiguity(
    tmp_path: Path,
) -> None:
    identifiers = iter(
        (
            "thread_aaaaaaaa111111111111111111111111",
            "thread_aaaaaaaa222222222222222222222222",
            "thread_bbbbbbbb333333333333333333333333",
        )
    )
    conversation = ConversationService(
        store=SQLiteConversationRepositories(tmp_path / "application.db"),
        id_factory=lambda prefix: next(identifiers),
    )
    first = conversation.create_thread("workspace_1", "First")
    second = conversation.create_thread("workspace_1", "Second")
    third = conversation.create_thread("workspace_1", "Third")

    async def delegate(intent: CommandIntent, thread_id: str) -> CommandResult:
        del intent, thread_id
        return CommandResult(status=CommandStatus.SUCCESS)

    commands = ConversationCommandService(
        conversation=conversation,
        workspace_key="workspace_1",
        delegate=delegate,
    )

    exact = await commands.handle(
        CommandIntent(name=CommandName.RESUME, arguments=(first.id,))
    )
    unique = await commands.handle(
        CommandIntent(name=CommandName.RESUME, arguments=("thread_bbbbbbbb",))
    )
    ambiguous = await commands.handle(
        CommandIntent(name=CommandName.RESUME, arguments=("thread_aaaaaaaa",))
    )
    too_short = await commands.handle(
        CommandIntent(name=CommandName.RESUME, arguments=("thread_bbbbbbb",))
    )

    assert exact.data["thread_id"] == first.id
    assert unique.data["thread_id"] == third.id
    assert ambiguous.selection is not None
    assert {option.value for option in ambiguous.selection.options} == {
        first.id,
        second.id,
    }
    assert too_short.data["error_code"] == "thread_not_found"
