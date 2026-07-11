from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from awesome_agent.conversation.models import (
    Thread,
    ThreadEntry,
    ThreadListPage,
    ThreadPage,
    ThreadSummary,
    ThreadView,
    ToolActivity,
    Turn,
    TurnStatus,
)


class ConversationError(RuntimeError):
    pass


class ThreadNotFound(ConversationError):
    pass


class TurnNotFound(ConversationError):
    pass


class TurnBusy(ConversationError):
    pass


class InvalidTurnTransition(ConversationError):
    pass


class ConversationConflict(ConversationError):
    pass


class ThreadRepository(Protocol):
    def create(self, thread: Thread) -> Thread: ...
    def get(self, thread_id: str) -> Thread | None: ...
    def list(self, workspace_key: str) -> Sequence[Thread]: ...
    def update(self, thread: Thread) -> Thread: ...


class ThreadEntryRepository(Protocol):
    def append(self, entry: ThreadEntry) -> ThreadEntry: ...
    def list(self, thread_id: str) -> Sequence[ThreadEntry]: ...


class TurnRepository(Protocol):
    def create(self, turn: Turn) -> Turn: ...
    def get(self, turn_id: str) -> Turn | None: ...
    def list(self, thread_id: str) -> Sequence[Turn]: ...
    def update(self, turn: Turn) -> Turn: ...
    def in_progress(self, thread_id: str) -> Turn | None: ...


class ThreadSummaryRepository(Protocol):
    def get(self, thread_id: str) -> ThreadSummary | None: ...
    def upsert(self, summary: ThreadSummary) -> ThreadSummary: ...


class ToolActivityRepository(Protocol):
    def append(self, activity: ToolActivity) -> ToolActivity: ...
    def list(self, thread_id: str) -> Sequence[ToolActivity]: ...


class ConversationStore(Protocol):
    def create_thread(self, thread: Thread) -> Thread: ...
    def update_thread(self, thread: Thread) -> Thread: ...
    def list_threads(self, workspace_key: str) -> Sequence[Thread]: ...
    def list_threads_page(
        self,
        workspace_key: str,
        *,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> ThreadListPage: ...
    def read_thread(self, thread_id: str) -> ThreadView: ...
    def read_thread_page(
        self,
        thread_id: str,
        *,
        before_sequence: int | None,
        limit: int,
    ) -> ThreadPage: ...
    def thread_id_for_turn(self, turn_id: str) -> str | None: ...
    def begin_turn(self, user_entry: ThreadEntry, turn: Turn) -> Turn: ...
    def complete_turn(self, assistant_entry: ThreadEntry, turn: Turn) -> Turn: ...
    def update_terminal_turn(self, turn: Turn) -> Turn: ...
    def append_direct_command(self, entry: ThreadEntry) -> ThreadEntry: ...
    def compare_and_swap_summary(
        self,
        summary: ThreadSummary,
        *,
        expected: ThreadSummary | None,
    ) -> ThreadSummary: ...


def require_turn_transition(current: TurnStatus, target: TurnStatus) -> None:
    if current is not TurnStatus.IN_PROGRESS or target not in {
        TurnStatus.COMPLETED,
        TurnStatus.FAILED,
        TurnStatus.CANCELLED,
    }:
        raise InvalidTurnTransition(
            f"Cannot transition Turn from {current} to {target}."
        )
