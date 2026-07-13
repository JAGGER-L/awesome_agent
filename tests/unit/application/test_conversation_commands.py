from __future__ import annotations

from datetime import UTC, datetime

import pytest

from awesome_agent.application.command_results import CommandSelection
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.conversation_commands import ConversationCommandService
from awesome_agent.conversation import Thread


class ConversationStub:
    def __init__(self) -> None:
        self.thread = Thread(
            id="thread_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            workspace_key="workspace_1",
            title="Fixture Thread",
            current_model="deepseek/deepseek-v4-flash",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    def create_thread(
        self, workspace_key: str, title: str | None, **_: object
    ) -> Thread:
        assert workspace_key == "workspace_1"
        if title:
            self.thread = self.thread.model_copy(update={"title": title})
        return self.thread

    def read_thread(self, thread_id: str) -> _ReadResult:
        assert thread_id == self.thread.id
        return _ReadResult(self.thread)

    def set_thinking(self, thread_id: str, enabled: bool) -> Thread:
        assert thread_id == self.thread.id
        self.thread = self.thread.model_copy(update={"thinking_enabled": enabled})
        return self.thread


class _ReadResult:
    def __init__(self, thread: Thread) -> None:
        self.thread = thread


@pytest.mark.asyncio
async def test_new_and_thinking_return_typed_outcomes() -> None:
    service = ConversationCommandService(
        conversation=ConversationStub(),  # type: ignore[arg-type]
        workspace_key="workspace_1",
    )
    created = await service.new(CommandIntent(name=CommandName.NEW))
    shown = await service.thinking(CommandIntent(name=CommandName.THINKING))

    assert created.kind == "result"
    assert created.payload.kind == "thread"
    assert created.payload.action == "created"
    assert shown.kind == "interaction"
    assert isinstance(shown.interaction, CommandSelection)


@pytest.mark.asyncio
async def test_thinking_requires_selected_thread() -> None:
    service = ConversationCommandService(
        conversation=ConversationStub(),  # type: ignore[arg-type]
        workspace_key="workspace_1",
    )
    outcome = await service.thinking(CommandIntent(name=CommandName.THINKING))
    assert outcome.kind == "error"
    assert outcome.code == "thread_not_found"
