from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Literal

from awesome_agent.application.command_results import (
    CommandError,
    CommandOption,
    CommandOutcome,
    CommandSelection,
    ThinkingCommandPayload,
    ThreadRenamedPayload,
    ThreadRetryCommandPayload,
    ThreadTransitionCommandPayload,
    ThreadTransitionSnapshot,
    error,
    interaction,
    result,
)
from awesome_agent.application.commands import CommandIntent
from awesome_agent.application.contracts import (
    ApplicationState,
    OperationAccepted,
    ThreadReadQuery,
    ThreadReadResult,
)
from awesome_agent.application.operations import OperationBusy
from awesome_agent.application.turns import TurnCoordinator
from awesome_agent.conversation import (
    ConversationConflict,
    ConversationService,
    InvalidTurnTransition,
    RetryPreparation,
    Thread,
    ThreadNotFound,
    TurnNotFound,
)


class ConversationCommandService:
    """Own selected Thread and future-Turn conversation controls."""

    def __init__(
        self,
        *,
        conversation: ConversationService,
        turns: TurnCoordinator,
        workspace_key: str,
        application_snapshot: Callable[[], Awaitable[ApplicationState]],
        thread_snapshot: Callable[[ThreadReadQuery], Awaitable[ThreadReadResult]],
        has_active_operation: Callable[[], bool],
        default_model: Callable[[], str | None] = lambda: None,
        on_thread_selected: Callable[[], None] = lambda: None,
        selected_thread_id: str | None = None,
    ) -> None:
        self._conversation = conversation
        self._turns = turns
        self._workspace_key = workspace_key
        self._application_snapshot = application_snapshot
        self._thread_snapshot = thread_snapshot
        self._has_active_operation = has_active_operation
        self._default_model = default_model
        self._on_thread_selected = on_thread_selected
        self._current_thread_id = selected_thread_id

    @property
    def current_thread_id(self) -> str | None:
        return self._current_thread_id

    async def select_recovery_thread(self, thread_id: str) -> None:
        thread = (await self._conversation.read_thread(thread_id)).thread
        if thread.workspace_key != self._workspace_key:
            raise ThreadNotFound(thread_id)
        self._select(thread)

    async def new(self, intent: CommandIntent) -> CommandOutcome:
        if intent.arguments:
            return error("invalid_arguments", "Usage: /new")
        if self._has_active_operation():
            return self._operation_busy()
        thread = await self._conversation.create_thread(
            self._workspace_key,
            None,
            current_model=self._default_model(),
        )
        return await self._transition(thread, reason="new")

    async def rename(self, intent: CommandIntent) -> CommandOutcome:
        thread = await self._selected_thread()
        if thread is None:
            return error("thread_not_found", "Select a Thread first.")
        title = " ".join(intent.arguments)
        if not title.strip():
            return error("invalid_arguments", "Title required · /rename <title>")
        try:
            renamed = await self._conversation.rename_thread(thread.id, title)
        except ValueError as exc:
            return error("invalid_arguments", str(exc))
        return result(ThreadRenamedPayload(thread=renamed))

    async def resume(self, intent: CommandIntent) -> CommandOutcome:
        if self._has_active_operation():
            return self._operation_busy()
        if len(intent.arguments) > 1:
            return error("invalid_arguments", "Usage: /resume [thread_id]")
        if not intent.arguments:
            page = await self._conversation.list_thread_page(
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
        matches = await self._matches(intent.arguments[0])
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
        return await self._transition(thread, reason="resume")

    async def fork(self, intent: CommandIntent) -> CommandOutcome:
        if self._has_active_operation():
            return self._operation_busy()
        parsed = self._materialization_target(intent, command="fork")
        if isinstance(parsed, CommandError):
            return parsed
        source_thread_id, source_turn_id = parsed
        try:
            view = await self._conversation.fork_thread(
                self._workspace_key,
                source_thread_id,
                source_turn_id,
            )
        except ThreadNotFound:
            return error("thread_not_found", "Source Thread was not found.")
        except TurnNotFound:
            return error("turn_not_found", "Turn was not found.")
        except InvalidTurnTransition:
            return error("invalid_arguments", "Fork requires a terminal Turn.")
        except ConversationConflict:
            return error(
                "conversation_conflict",
                "The source Thread changed; retry the fork.",
            )
        return await self._transition(view.thread, reason="fork")

    async def retry(self, intent: CommandIntent) -> CommandOutcome:
        if self._has_active_operation():
            return self._operation_busy()
        parsed = self._materialization_target(intent, command="retry")
        if isinstance(parsed, CommandError):
            return parsed
        source_thread_id, source_turn_id = parsed

        async def started(
            preparation: RetryPreparation,
            operation: OperationAccepted,
        ) -> CommandOutcome:
            transition = await self._prepare_transition(
                preparation.view.thread,
                reason="retry",
            )
            outcome = result(
                ThreadRetryCommandPayload(
                    transition=transition,
                    operation=operation,
                )
            )
            self._select(preparation.view.thread, notify=False)
            return outcome

        try:
            return await self._turns.retry_turn(
                source_thread_id,
                source_turn_id,
                before_start=self._on_thread_selected,
                started=started,
            )
        except OperationBusy:
            return self._operation_busy()
        except ThreadNotFound:
            return error("thread_not_found", "Source Thread was not found.")
        except TurnNotFound:
            return error("turn_not_found", "Turn was not found.")
        except InvalidTurnTransition:
            return error("invalid_arguments", "Retry requires a terminal Turn.")
        except ConversationConflict:
            return error(
                "conversation_conflict",
                "The source Thread changed; retry the command.",
            )

    async def thinking(self, intent: CommandIntent) -> CommandOutcome:
        thread = await self._selected_thread()
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
        updated = await self._conversation.set_thinking(
            thread.id, intent.arguments[0] == "on"
        )
        return result(ThinkingCommandPayload(enabled=updated.thinking_enabled))

    async def _matches(self, requested: str) -> list[Thread]:
        try:
            exact = (await self._conversation.read_thread(requested)).thread
        except ThreadNotFound:
            exact = None
        if exact is not None and exact.workspace_key == self._workspace_key:
            return [exact]
        if re.fullmatch(r"thread_[a-f0-9]{8,32}", requested):
            return list(
                await self._conversation.match_thread_prefix(
                    self._workspace_key, prefix=requested, limit=200
                )
            )
        return []

    def _select(self, thread: Thread, *, notify: bool = True) -> None:
        self._current_thread_id = thread.id
        if notify:
            self._on_thread_selected()

    async def _transition(
        self,
        thread: Thread,
        *,
        reason: Literal["new", "resume", "fork"],
    ) -> CommandOutcome:
        transition = await self._prepare_transition(thread, reason=reason)
        outcome = result(ThreadTransitionCommandPayload(transition=transition))
        self._select(thread)
        return outcome

    async def _prepare_transition(
        self,
        thread: Thread,
        *,
        reason: Literal["new", "resume", "fork", "retry"],
    ) -> ThreadTransitionSnapshot:
        page = await self._thread_snapshot(
            ThreadReadQuery(thread_id=thread.id, limit=100)
        )
        application = await self._application_snapshot()
        application = application.model_copy(update={"current_thread_id": thread.id})
        return ThreadTransitionSnapshot(
            reason=reason,
            application=application,
            thread=page,
        )

    def _materialization_target(
        self,
        intent: CommandIntent,
        *,
        command: Literal["fork", "retry"],
    ) -> tuple[str, str | None] | CommandError:
        if len(intent.arguments) > 1:
            return error("invalid_arguments", f"Usage: /{command} [turn_id]")
        if self._current_thread_id is None:
            return error("thread_not_found", "Select a Thread first.")
        source_turn_id = intent.arguments[0] if intent.arguments else None
        if (
            source_turn_id is not None
            and re.fullmatch(r"turn_[a-f0-9]{8,32}", source_turn_id) is None
        ):
            return error(
                "invalid_arguments",
                f"Usage: /{command} [turn_id]",
            )
        return self._current_thread_id, source_turn_id

    @staticmethod
    def _operation_busy() -> CommandOutcome:
        return error(
            "operation_busy",
            "Stop the current task before starting or resuming a conversation.",
        )

    async def _selected_thread(self) -> Thread | None:
        if self._current_thread_id is None:
            return None
        return (await self._conversation.read_thread(self._current_thread_id)).thread
