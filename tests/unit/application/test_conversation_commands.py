from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from awesome_agent.application.command_results import CommandSelection
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.contracts import (
    ApplicationState,
    OperationAccepted,
    ThreadReadQuery,
    ThreadReadResult,
)
from awesome_agent.application.conversation_commands import ConversationCommandService
from awesome_agent.application.turns import TurnCoordinator
from awesome_agent.config.models import BudgetConfig, SecretStatus
from awesome_agent.conversation import (
    InvalidTurnTransition,
    RetryPreparation,
    Thread,
    ThreadEntry,
    ThreadEntryKind,
    ThreadLineage,
    ThreadTitleSource,
    ThreadView,
    Turn,
    TurnStatus,
)
from awesome_agent.core.tools.permissions import PermissionMode


class ConversationStub:
    def __init__(self) -> None:
        self.create_calls = 0
        self.fork_calls: list[tuple[str, str, str | None]] = []
        self.fork_error: Exception | None = None
        self.thread = Thread(
            id="thread_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            workspace_key="workspace_1",
            title="Fixture Thread",
            current_model="deepseek/deepseek-v4-flash",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.view = ThreadView(thread=self.thread)

    async def create_thread(
        self, workspace_key: str, title: str | None, **_: object
    ) -> Thread:
        assert workspace_key == "workspace_1"
        self.create_calls += 1
        if title:
            self.thread = self.thread.model_copy(update={"title": title})
        self.view = ThreadView(thread=self.thread)
        return self.thread

    async def read_thread(self, thread_id: str) -> _ReadResult:
        assert thread_id == self.thread.id
        return _ReadResult(self.thread)

    async def set_thinking(self, thread_id: str, enabled: bool) -> Thread:
        assert thread_id == self.thread.id
        self.thread = self.thread.model_copy(update={"thinking_enabled": enabled})
        self.view = ThreadView(thread=self.thread)
        return self.thread

    async def rename_thread(self, thread_id: str, title: str) -> Thread:
        assert thread_id == self.thread.id
        self.thread = self.thread.model_copy(
            update={
                "title": title,
                "title_source": ThreadTitleSource.MANUAL,
            }
        )
        self.view = ThreadView(thread=self.thread)
        return self.thread

    async def fork_thread(
        self,
        workspace_key: str,
        source_thread_id: str,
        source_turn_id: str | None,
    ) -> ThreadView:
        self.fork_calls.append((workspace_key, source_thread_id, source_turn_id))
        if self.fork_error is not None:
            raise self.fork_error
        self.thread = self.thread.model_copy(
            update={
                "id": "thread_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "title": "Fork of Fixture Thread",
                "lineage": ThreadLineage(
                    kind="fork",
                    source_thread_id=source_thread_id,
                    source_turn_id=(
                        source_turn_id or "turn_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    ),
                ),
            }
        )
        self.view = ThreadView(thread=self.thread)
        return self.view

    def retry_preparation(
        self,
        source_thread_id: str,
        source_turn_id: str | None,
    ) -> RetryPreparation:
        now = datetime.now(UTC)
        self.thread = self.thread.model_copy(
            update={
                "id": "thread_cccccccccccccccccccccccccccccccc",
                "title": "Retry of Fixture Thread",
                "lineage": ThreadLineage(
                    kind="retry",
                    source_thread_id=source_thread_id,
                    source_turn_id=(
                        source_turn_id or "turn_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    ),
                ),
            }
        )
        entry = ThreadEntry(
            id="entry_cccccccccccccccccccccccccccccccc",
            thread_id=self.thread.id,
            sequence=1,
            kind=ThreadEntryKind.USER_MESSAGE,
            content="retry content",
            client_message_id="client_retry",
            created_at=now,
        )
        turn = Turn(
            id="turn_cccccccccccccccccccccccccccccccc",
            thread_id=self.thread.id,
            checkpoint_key="turn_cccccccccccccccccccccccccccccccc",
            status=TurnStatus.IN_PROGRESS,
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            budgets=BudgetConfig(),
            user_entry_id=entry.id,
            created_at=now,
            updated_at=now,
        )
        view = ThreadView(thread=self.thread, entries=(entry,), turns=(turn,))
        self.view = view
        return RetryPreparation(
            view=view,
            turn=turn,
            content=entry.content,
            client_message_id="client_retry",
        )


class RetryTurnStub:
    def __init__(self, conversation: ConversationStub) -> None:
        self._conversation = conversation
        self.calls: list[tuple[str, str | None]] = []
        self.error: Exception | None = None

    async def retry_turn(
        self,
        source_thread_id: str,
        source_turn_id: str | None,
        *,
        before_start: Callable[[], None],
        started: Callable[
            [RetryPreparation, OperationAccepted], Awaitable[Any]
        ],
    ) -> Any:
        self.calls.append((source_thread_id, source_turn_id))
        if self.error is not None:
            raise self.error
        preparation = self._conversation.retry_preparation(
            source_thread_id,
            source_turn_id,
        )
        before_start()
        return await started(
            preparation,
            OperationAccepted(
                operation_id="operation_cccccccccccccccccccccccccccccccc",
                thread_id=preparation.turn.thread_id,
                turn_id=preparation.turn.id,
                client_message_id=preparation.client_message_id,
            ),
        )


class _ReadResult:
    def __init__(self, thread: Thread) -> None:
        self.thread = thread


def _application_snapshot(thread_id: str) -> ApplicationState:
    return ApplicationState.model_construct(
        initialized=True,
        session_id="session_fixture",
        workspace_key="workspace_1",
        workspace={"display_path": "E:/fixture"},
        workspace_trusted=True,
        current_thread_id=thread_id,
        thinking_enabled=False,
        skill_mode="auto",
        permission_mode=PermissionMode.REQUEST_APPROVAL,
        configuration_valid=True,
        secret_status=SecretStatus(),
    )


def _thread_snapshot(thread: Thread) -> ThreadReadResult:
    return ThreadReadResult(view=ThreadView(thread=thread))


def conversation_service(
    *,
    active: bool,
    conversation: ConversationStub | None = None,
    turns: RetryTurnStub | None = None,
    selected: bool = False,
    on_thread_selected: Callable[[], None] = lambda: None,
) -> ConversationCommandService:
    stub = conversation or ConversationStub()
    turn_stub = turns or RetryTurnStub(stub)

    async def application_snapshot() -> ApplicationState:
        return _application_snapshot(stub.thread.id)

    async def thread_snapshot(query: ThreadReadQuery) -> ThreadReadResult:
        assert query.thread_id == stub.thread.id
        return ThreadReadResult(view=stub.view)

    return ConversationCommandService(
        conversation=stub,  # type: ignore[arg-type]
        turns=cast(TurnCoordinator, turn_stub),
        workspace_key="workspace_1",
        application_snapshot=application_snapshot,
        thread_snapshot=thread_snapshot,
        has_active_operation=lambda: active,
        on_thread_selected=on_thread_selected,
        selected_thread_id=stub.thread.id if selected else None,
    )


@pytest.mark.asyncio
async def test_new_and_thinking_return_typed_outcomes() -> None:
    service = conversation_service(active=False)
    created = await service.new(CommandIntent(name=CommandName.NEW))
    shown = await service.thinking(CommandIntent(name=CommandName.THINKING))

    assert created.kind == "result"
    assert created.payload.kind == "thread_transition"
    assert created.payload.transition.reason == "new"
    assert (
        created.payload.transition.application.current_thread_id
        == created.payload.transition.thread.view.thread.id
    )
    assert shown.kind == "interaction"
    assert isinstance(shown.interaction, CommandSelection)


@pytest.mark.asyncio
async def test_thinking_requires_selected_thread() -> None:
    service = conversation_service(active=False)
    outcome = await service.thinking(CommandIntent(name=CommandName.THINKING))
    assert outcome.kind == "error"
    assert outcome.code == "thread_not_found"


@pytest.mark.asyncio
async def test_new_and_resume_reject_before_mutation_while_operation_active() -> None:
    stub = ConversationStub()
    service = conversation_service(active=True, conversation=stub)

    created = await service.new(CommandIntent(name=CommandName.NEW))
    resumed = await service.resume(CommandIntent(name=CommandName.RESUME))

    assert created.kind == resumed.kind == "error"
    assert created.code == resumed.code == "operation_busy"
    assert created.message == (
        "Stop the current task before starting or resuming a conversation."
    )
    assert stub.create_calls == 0


@pytest.mark.asyncio
async def test_rename_updates_title_and_marks_it_manual() -> None:
    service = conversation_service(active=False)
    await service.new(CommandIntent(name=CommandName.NEW))

    outcome = await service.rename(
        CommandIntent(name=CommandName.RENAME, arguments=("Cube", "helper"))
    )

    assert outcome.kind == "result"
    assert outcome.payload.kind == "thread_renamed"
    assert outcome.payload.thread.title == "Cube helper"
    assert outcome.payload.thread.title_source is ThreadTitleSource.MANUAL


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [(), ("   ",)])
async def test_rename_requires_a_title(arguments: tuple[str, ...]) -> None:
    service = conversation_service(active=False)
    await service.new(CommandIntent(name=CommandName.NEW))

    outcome = await service.rename(
        CommandIntent(name=CommandName.RENAME, arguments=arguments)
    )

    assert outcome.kind == "error"
    assert outcome.code == "invalid_arguments"
    assert outcome.message == "Title required · /rename <title>"


@pytest.mark.asyncio
async def test_new_rejects_hidden_title_arguments() -> None:
    stub = ConversationStub()
    service = conversation_service(active=False, conversation=stub)

    outcome = await service.new(
        CommandIntent(name=CommandName.NEW, arguments=("unexpected",))
    )

    assert outcome.kind == "error"
    assert outcome.code == "invalid_arguments"
    assert stub.create_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "turn_id",
    [None, "turn_12345678"],
)
async def test_fork_materializes_and_selects_default_or_explicit_terminal_turn(
    turn_id: str | None,
) -> None:
    stub = ConversationStub()
    selected: list[str] = []
    service = conversation_service(
        active=False,
        conversation=stub,
        selected=True,
        on_thread_selected=lambda: selected.append(stub.thread.id),
    )
    source_thread_id = stub.thread.id
    arguments = () if turn_id is None else (turn_id,)

    outcome = await service.fork(
        CommandIntent(name=CommandName.FORK, arguments=arguments)
    )

    assert outcome.kind == "result"
    assert outcome.payload.kind == "thread_transition"
    assert outcome.payload.transition.reason == "fork"
    assert outcome.payload.transition.thread.view.thread.lineage == ThreadLineage(
        kind="fork",
        source_thread_id=source_thread_id,
        source_turn_id=turn_id or "turn_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    assert stub.fork_calls == [("workspace_1", source_thread_id, turn_id)]
    assert service.current_thread_id == stub.thread.id
    assert selected == [stub.thread.id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [("not-a-turn",), ("turn_12345678", "turn_abcdef12")],
)
async def test_fork_and_retry_reject_invalid_arguments_without_mutation(
    arguments: tuple[str, ...],
) -> None:
    stub = ConversationStub()
    turns = RetryTurnStub(stub)
    service = conversation_service(
        active=False,
        conversation=stub,
        turns=turns,
        selected=True,
    )

    forked = await service.fork(
        CommandIntent(name=CommandName.FORK, arguments=arguments)
    )
    retried = await service.retry(
        CommandIntent(name=CommandName.RETRY, arguments=arguments)
    )

    assert forked.kind == retried.kind == "error"
    assert forked.code == retried.code == "invalid_arguments"
    assert stub.fork_calls == []
    assert turns.calls == []


@pytest.mark.asyncio
async def test_fork_and_retry_reject_nonterminal_turns() -> None:
    stub = ConversationStub()
    turns = RetryTurnStub(stub)
    stub.fork_error = InvalidTurnTransition("terminal required")
    turns.error = InvalidTurnTransition("terminal required")
    service = conversation_service(
        active=False,
        conversation=stub,
        turns=turns,
        selected=True,
    )

    forked = await service.fork(CommandIntent(name=CommandName.FORK))
    retried = await service.retry(CommandIntent(name=CommandName.RETRY))

    assert forked.kind == retried.kind == "error"
    assert forked.code == retried.code == "invalid_arguments"
    assert "terminal" in forked.message
    assert "terminal" in retried.message


@pytest.mark.asyncio
async def test_retry_returns_one_identity_bound_operation_and_selects_once() -> None:
    stub = ConversationStub()
    turns = RetryTurnStub(stub)
    selected: list[str] = []
    service = conversation_service(
        active=False,
        conversation=stub,
        turns=turns,
        selected=True,
        on_thread_selected=lambda: selected.append(stub.thread.id),
    )
    source_thread_id = stub.thread.id

    outcome = await service.retry(CommandIntent(name=CommandName.RETRY))

    assert outcome.kind == "result"
    assert outcome.payload.kind == "thread_retry"
    transition = outcome.payload.transition
    operation = outcome.payload.operation
    assert transition.reason == "retry"
    assert transition.thread.view.thread.lineage == ThreadLineage(
        kind="retry",
        source_thread_id=source_thread_id,
        source_turn_id="turn_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    assert transition.thread.view.thread.id == operation.thread_id
    assert operation.turn_id == transition.thread.view.turns[-1].id
    assert turns.calls == [(source_thread_id, None)]
    assert service.current_thread_id == operation.thread_id
    assert selected == [operation.thread_id]


@pytest.mark.asyncio
async def test_fork_and_retry_reject_before_materialization_while_active() -> None:
    stub = ConversationStub()
    turns = RetryTurnStub(stub)
    service = conversation_service(
        active=True,
        conversation=stub,
        turns=turns,
        selected=True,
    )

    forked = await service.fork(CommandIntent(name=CommandName.FORK))
    retried = await service.retry(CommandIntent(name=CommandName.RETRY))

    assert forked.kind == retried.kind == "error"
    assert forked.code == retried.code == "operation_busy"
    assert stub.fork_calls == []
    assert turns.calls == []
