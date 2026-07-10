from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import JsonValue

from awesome_agent.config.models import TurnConfig
from awesome_agent.conversation.models import (
    Thread,
    ThreadEntry,
    ThreadEntryKind,
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
                created_at=now,
                updated_at=now,
            )
        )

    def list_threads(self, workspace_key: str) -> tuple[Thread, ...]:
        return tuple(self._store.list_threads(workspace_key))

    def read_thread(self, thread_id: str) -> ThreadView:
        return self._store.read_thread(thread_id)

    def begin_turn(
        self,
        thread_id: str,
        user_content: str,
        config: TurnConfig,
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
    ) -> Turn:
        view, current = self._turn_view(turn_id)
        if current.status is TurnStatus.COMPLETED:
            entry = _entry_by_id(view, current.assistant_entry_id)
            if (
                entry is not None
                and entry.content == assistant_content
                and current.usage == usage
                and current.termination_reason == termination_reason
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
                "updated_at": now,
                "completed_at": now,
            }
        )
        completed = Turn.model_validate(completed.model_dump())
        return self._store.complete_turn(assistant, completed)

    def fail_turn(self, turn_id: str, error_code: str) -> Turn:
        if not error_code.strip():
            raise ValueError("error_code cannot be empty.")
        _, current = self._turn_view(turn_id)
        if current.status is TurnStatus.FAILED:
            if current.error_code == error_code:
                return current
            raise ConversationConflict("Failed Turn finalization differs.")
        require_turn_transition(current.status, TurnStatus.FAILED)
        now = self._clock()
        failed = current.model_copy(
            update={
                "status": TurnStatus.FAILED,
                "error_code": error_code,
                "updated_at": now,
                "completed_at": now,
            }
        )
        failed = Turn.model_validate(failed.model_dump())
        return self._store.update_terminal_turn(failed)

    def cancel_turn(self, turn_id: str) -> Turn:
        _, current = self._turn_view(turn_id)
        if current.status is TurnStatus.CANCELLED:
            return current
        require_turn_transition(current.status, TurnStatus.CANCELLED)
        now = self._clock()
        cancelled = current.model_copy(
            update={
                "status": TurnStatus.CANCELLED,
                "termination_reason": "cancelled",
                "updated_at": now,
                "completed_at": now,
            }
        )
        cancelled = Turn.model_validate(cancelled.model_dump())
        return self._store.update_terminal_turn(cancelled)

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
