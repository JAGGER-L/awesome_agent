from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from awesome_agent.conversation.models import (
    Thread,
    ThreadEntry,
    ThreadSummary,
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


def require_turn_transition(current: TurnStatus, target: TurnStatus) -> None:
    if current is not TurnStatus.IN_PROGRESS or target not in {
        TurnStatus.COMPLETED,
        TurnStatus.FAILED,
        TurnStatus.CANCELLED,
    }:
        raise InvalidTurnTransition(
            f"Cannot transition Turn from {current} to {target}."
        )
