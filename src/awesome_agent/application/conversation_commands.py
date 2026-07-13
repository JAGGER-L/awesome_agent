from __future__ import annotations

import re
from collections.abc import Callable

from awesome_agent.application.command_results import (
    CommandOption,
    CommandOutcome,
    CommandSelection,
    ThinkingCommandPayload,
    ThreadCommandPayload,
    error,
    interaction,
    result,
)
from awesome_agent.application.commands import CommandIntent
from awesome_agent.conversation import ConversationService, Thread, ThreadNotFound


class ConversationCommandService:
    """Own selected Thread and future-Turn conversation controls."""

    def __init__(
        self,
        *,
        conversation: ConversationService,
        workspace_key: str,
        default_model: Callable[[], str | None] = lambda: None,
        on_thread_selected: Callable[[], None] = lambda: None,
    ) -> None:
        self._conversation = conversation
        self._workspace_key = workspace_key
        self._default_model = default_model
        self._on_thread_selected = on_thread_selected
        self._current_thread_id: str | None = None

    @property
    def current_thread_id(self) -> str | None:
        return self._current_thread_id

    async def new(self, intent: CommandIntent) -> CommandOutcome:
        title = " ".join(intent.arguments).strip() or None
        thread = self._conversation.create_thread(
            self._workspace_key,
            title,
            current_model=self._default_model(),
        )
        self._select(thread)
        return result(
            ThreadCommandPayload(
                action="created", thread_id=thread.id, title=thread.title
            )
        )

    async def resume(self, intent: CommandIntent) -> CommandOutcome:
        if len(intent.arguments) > 1:
            return error("invalid_arguments", "Usage: /resume [thread_id]")
        if not intent.arguments:
            page = self._conversation.list_thread_page(
                self._workspace_key, cursor=None, limit=200
            )
            if not page.threads:
                return error("thread_not_found", "No Threads are available.")
            return interaction(
                CommandSelection(
                    prompt="Select a Thread to resume.",
                    options=tuple(
                        CommandOption(
                            value=thread.id,
                            label=thread.title,
                            selected=thread.id == self._current_thread_id,
                        )
                        for thread in page.threads
                    ),
                )
            )
        matches = self._matches(intent.arguments[0])
        if not matches:
            return error("thread_not_found", "Thread was not found.")
        if len(matches) > 1:
            return interaction(
                CommandSelection(
                    prompt="Select a matching Thread to resume.",
                    options=tuple(
                        CommandOption(value=thread.id, label=thread.title)
                        for thread in matches
                    ),
                )
            )
        thread = matches[0]
        self._select(thread)
        return result(
            ThreadCommandPayload(
                action="resumed", thread_id=thread.id, title=thread.title
            )
        )

    async def thinking(self, intent: CommandIntent) -> CommandOutcome:
        thread = self._selected_thread()
        if thread is None:
            return error("thread_not_found", "Select a Thread first.")
        if not intent.arguments:
            return interaction(
                CommandSelection(
                    prompt="Select thinking mode for future Turns.",
                    options=(
                        CommandOption(
                            value="off",
                            label="Off",
                            selected=not thread.thinking_enabled,
                        ),
                        CommandOption(
                            value="on", label="On", selected=thread.thinking_enabled
                        ),
                    ),
                ),
                context=ThinkingCommandPayload(enabled=thread.thinking_enabled),
            )
        if len(intent.arguments) != 1 or intent.arguments[0] not in {"on", "off"}:
            return error("invalid_arguments", "Usage: /thinking [on|off]")
        updated = self._conversation.set_thinking(
            thread.id, intent.arguments[0] == "on"
        )
        return result(ThinkingCommandPayload(enabled=updated.thinking_enabled))

    def _matches(self, requested: str) -> list[Thread]:
        try:
            exact = self._conversation.read_thread(requested).thread
        except ThreadNotFound:
            exact = None
        if exact is not None and exact.workspace_key == self._workspace_key:
            return [exact]
        if re.fullmatch(r"thread_[a-f0-9]{8,32}", requested):
            return list(
                self._conversation.match_thread_prefix(
                    self._workspace_key, prefix=requested, limit=200
                )
            )
        return []

    def _select(self, thread: Thread) -> None:
        self._current_thread_id = thread.id
        self._on_thread_selected()

    def _selected_thread(self) -> Thread | None:
        if self._current_thread_id is None:
            return None
        return self._conversation.read_thread(self._current_thread_id).thread
