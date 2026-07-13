from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import JsonValue

from awesome_agent.config.models import TurnConfig
from awesome_agent.conversation.models import (
    Thread,
    ThreadEntry,
    ThreadEntryKind,
    ThreadListPage,
    ThreadPage,
    ThreadSummary,
    ThreadView,
    Turn,
    TurnStatus,
    UsageSummary,
)
from awesome_agent.conversation.repository import (
    ConversationConflict,
    ConversationStore,
    TurnNotFound,
    require_turn_transition,
)


class ConversationService:
    def __init__(
        self,
        *,
        store: ConversationStore,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._id_factory = id_factory or _new_identifier
        self._clock = clock or (lambda: datetime.now(UTC))

    def create_thread(
        self,
        workspace_key: str,
        title: str | None = None,
        *,
        current_model: str | None = None,
    ) -> Thread:
        normalized_title = "New conversation" if title is None else title.strip()
        if not normalized_title:
            raise ValueError("Thread title cannot be empty.")
        now = self._clock()
        return self._store.create_thread(
            Thread(
                id=self._id_factory("thread"),
                workspace_key=workspace_key,
                title=normalized_title,
                current_model=current_model,
                created_at=now,
                updated_at=now,
            )
        )

    def list_threads(self, workspace_key: str) -> tuple[Thread, ...]:
        return tuple(self._store.list_threads(workspace_key))

    def match_thread_prefix(
        self,
        workspace_key: str,
        *,
        prefix: str,
        limit: int = 200,
    ) -> tuple[Thread, ...]:
        return tuple(
            self._store.match_threads(
                workspace_key,
                prefix=prefix,
                limit=limit,
            )
        )

    def list_thread_page(
        self,
        workspace_key: str,
        *,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> ThreadListPage:
        return self._store.list_threads_page(
            workspace_key,
            cursor=cursor,
            limit=limit,
        )

    def set_skill_mode(self, thread_id: str, skill_mode: str) -> Thread:
        if re.fullmatch(r"(?:auto|off|[a-z][a-z0-9-]{0,63})", skill_mode) is None:
            raise ValueError("Skill mode is invalid.")
        current = self._store.read_thread(thread_id).thread
        updated = current.model_copy(
            update={"skill_mode": skill_mode, "updated_at": self._clock()}
        )
        return self._store.update_thread(Thread.model_validate(updated.model_dump()))

    def set_model(self, thread_id: str, model: str) -> Thread:
        current = self._store.read_thread(thread_id).thread
        updated = current.model_copy(
            update={"current_model": model, "updated_at": self._clock()}
        )
        return self._store.update_thread(Thread.model_validate(updated.model_dump()))

    def set_thinking(self, thread_id: str, enabled: bool) -> Thread:
        current = self._store.read_thread(thread_id).thread
        updated = current.model_copy(
            update={"thinking_enabled": enabled, "updated_at": self._clock()}
        )
        return self._store.update_thread(Thread.model_validate(updated.model_dump()))

    def read_thread(self, thread_id: str) -> ThreadView:
        return self._store.read_thread(thread_id)

    def read_thread_page(
        self,
        thread_id: str,
        *,
        before_sequence: int | None,
        limit: int,
    ) -> ThreadPage:
        return self._store.read_thread_page(
            thread_id,
            before_sequence=before_sequence,
            limit=limit,
        )

    def begin_turn(
        self,
        thread_id: str,
        user_content: str,
        config: TurnConfig,
        *,
        client_message_id: str,
    ) -> Turn:
        if not user_content.strip():
            raise ValueError("User message cannot be empty.")
        view = self._store.read_thread(thread_id)
        now = self._clock()
        entry = ThreadEntry(
            id=self._id_factory("entry"),
            thread_id=thread_id,
            sequence=_next_sequence(view),
            kind=ThreadEntryKind.USER_MESSAGE,
            content=user_content,
            client_message_id=client_message_id,
            created_at=now,
        )
        turn_id = self._id_factory("turn")
        turn = Turn(
            id=turn_id,
            thread_id=thread_id,
            checkpoint_key=turn_id,
            status=TurnStatus.IN_PROGRESS,
            provider=config.provider,
            model=config.model,
            thinking_enabled=config.thinking_enabled,
            skill_mode=config.skill_mode,
            budgets=config.budgets,
            user_entry_id=entry.id,
            created_at=now,
            updated_at=now,
        )
        return self._store.begin_turn(entry, turn)

    def complete_turn(
        self,
        turn_id: str,
        assistant_content: str,
        usage: UsageSummary,
        termination_reason: str,
        context_manifest: tuple[dict[str, JsonValue], ...] = (),
    ) -> Turn:
        view, current = self._turn_view(turn_id)
        if current.status is TurnStatus.COMPLETED:
            entry = _entry_by_id(view, current.assistant_entry_id)
            if (
                entry is not None
                and entry.content == assistant_content
                and current.usage == usage
                and current.termination_reason == termination_reason
                and current.context_manifest == context_manifest
            ):
                return current
            raise ConversationConflict("Completed Turn finalization differs.")
        require_turn_transition(current.status, TurnStatus.COMPLETED)
        now = self._clock()
        assistant = ThreadEntry(
            id=self._id_factory("entry"),
            thread_id=current.thread_id,
            sequence=_next_sequence(view),
            kind=ThreadEntryKind.ASSISTANT_MESSAGE,
            content=assistant_content,
            created_at=now,
        )
        completed = current.model_copy(
            update={
                "status": TurnStatus.COMPLETED,
                "assistant_entry_id": assistant.id,
                "usage": usage,
                "termination_reason": termination_reason,
                "context_manifest": context_manifest,
                "updated_at": now,
                "completed_at": now,
            }
        )
        completed = Turn.model_validate(completed.model_dump())
        return self._store.complete_turn(assistant, completed)

    def fail_turn(
        self,
        turn_id: str,
        error_code: str,
        *,
        usage: UsageSummary | None = None,
        context_manifest: tuple[dict[str, JsonValue], ...] = (),
    ) -> Turn:
        if not error_code.strip():
            raise ValueError("error_code cannot be empty.")
        observed_usage = usage or UsageSummary()
        _, current = self._turn_view(turn_id)
        if current.status is TurnStatus.FAILED:
            if (
                current.error_code == error_code
                and current.usage == observed_usage
                and current.context_manifest == context_manifest
            ):
                return current
            raise ConversationConflict("Failed Turn finalization differs.")
        require_turn_transition(current.status, TurnStatus.FAILED)
        now = self._clock()
        failed = current.model_copy(
            update={
                "status": TurnStatus.FAILED,
                "error_code": error_code,
                "usage": observed_usage,
                "context_manifest": context_manifest,
                "updated_at": now,
                "completed_at": now,
            }
        )
        failed = Turn.model_validate(failed.model_dump())
        return self._store.update_terminal_turn(failed)

    def cancel_turn(
        self,
        turn_id: str,
        *,
        usage: UsageSummary | None = None,
        context_manifest: tuple[dict[str, JsonValue], ...] = (),
    ) -> Turn:
        observed_usage = usage or UsageSummary()
        _, current = self._turn_view(turn_id)
        if current.status is TurnStatus.CANCELLED:
            if (
                current.usage == observed_usage
                and current.context_manifest == context_manifest
            ):
                return current
            raise ConversationConflict("Cancelled Turn finalization differs.")
        require_turn_transition(current.status, TurnStatus.CANCELLED)
        now = self._clock()
        cancelled = current.model_copy(
            update={
                "status": TurnStatus.CANCELLED,
                "termination_reason": "cancelled",
                "usage": observed_usage,
                "context_manifest": context_manifest,
                "updated_at": now,
                "completed_at": now,
            }
        )
        cancelled = Turn.model_validate(cancelled.model_dump())
        return self._store.update_terminal_turn(cancelled)

    def thread_usage(self, thread_id: str) -> UsageSummary:
        total = UsageSummary()
        for turn in self._store.read_thread(thread_id).turns:
            total += turn.usage
        return total

    def latest_context_manifest(
        self,
        thread_id: str,
    ) -> tuple[dict[str, JsonValue], ...]:
        turns = self._store.read_thread(thread_id).turns
        return next(
            (
                turn.context_manifest
                for turn in reversed(turns)
                if turn.context_manifest
            ),
            (),
        )

    def append_direct_command(
        self,
        thread_id: str,
        content: str,
        metadata: dict[str, JsonValue],
    ) -> ThreadEntry:
        view = self._store.read_thread(thread_id)
        entry = ThreadEntry(
            id=self._id_factory("entry"),
            thread_id=thread_id,
            sequence=_next_sequence(view),
            kind=ThreadEntryKind.DIRECT_COMMAND,
            content=content,
            metadata=metadata,
            created_at=self._clock(),
        )
        return self._store.append_direct_command(entry)

    def store_summary(
        self,
        summary: ThreadSummary,
        *,
        expected: ThreadSummary | None,
    ) -> ThreadSummary:
        return self._store.compare_and_swap_summary(summary, expected=expected)

    def _turn_view(self, turn_id: str) -> tuple[ThreadView, Turn]:
        thread_id = self._thread_id_for_turn(turn_id)
        view = self._store.read_thread(thread_id)
        current = next((turn for turn in view.turns if turn.id == turn_id), None)
        if current is None:
            raise TurnNotFound(turn_id)
        return view, current

    def _thread_id_for_turn(self, turn_id: str) -> str:
        thread_id = self._store.thread_id_for_turn(turn_id)
        if thread_id is None:
            raise TurnNotFound(turn_id)
        return thread_id


def _next_sequence(view: ThreadView) -> int:
    return 1 if not view.entries else view.entries[-1].sequence + 1


def _entry_by_id(view: ThreadView, entry_id: str | None) -> ThreadEntry | None:
    if entry_id is None:
        return None
    return next((entry for entry in view.entries if entry.id == entry_id), None)


def _new_identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"
