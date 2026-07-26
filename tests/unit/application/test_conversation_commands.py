from __future__ import annotations

from datetime import UTC, datetime

import pytest

from awesome_agent.application.command_results import CommandSelection
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.contracts import (
    ApplicationState,
    ThreadReadQuery,
    ThreadReadResult,
)
from awesome_agent.application.conversation_commands import ConversationCommandService
from awesome_agent.config.models import SecretStatus
from awesome_agent.conversation import Thread, ThreadTitleSource, ThreadView
from awesome_agent.core.tools.permissions import PermissionMode


class ConversationStub:
    def __init__(self) -> None:
        self.create_calls = 0
        self.thread = Thread(
            id="thread_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            workspace_key="workspace_1",
            title="Fixture Thread",
            current_model="deepseek/deepseek-v4-flash",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def create_thread(
        self, workspace_key: str, title: str | None, **_: object
    ) -> Thread:
        assert workspace_key == "workspace_1"
        self.create_calls += 1
        if title:
            self.thread = self.thread.model_copy(update={"title": title})
        return self.thread

    async def read_thread(self, thread_id: str) -> _ReadResult:
        assert thread_id == self.thread.id
        return _ReadResult(self.thread)

    async def set_thinking(self, thread_id: str, enabled: bool) -> Thread:
        assert thread_id == self.thread.id
        self.thread = self.thread.model_copy(update={"thinking_enabled": enabled})
        return self.thread

    async def rename_thread(self, thread_id: str, title: str) -> Thread:
        assert thread_id == self.thread.id
        self.thread = self.thread.model_copy(
            update={
                "title": title,
                "title_source": ThreadTitleSource.MANUAL,
            }
        )
        return self.thread


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
) -> ConversationCommandService:
    stub = conversation or ConversationStub()

    async def application_snapshot() -> ApplicationState:
        return _application_snapshot(stub.thread.id)

    async def thread_snapshot(query: ThreadReadQuery) -> ThreadReadResult:
        assert query.thread_id == stub.thread.id
        return _thread_snapshot(stub.thread)

    return ConversationCommandService(
        conversation=stub,  # type: ignore[arg-type]
        workspace_key="workspace_1",
        application_snapshot=application_snapshot,
        thread_snapshot=thread_snapshot,
        has_active_operation=lambda: active,
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
