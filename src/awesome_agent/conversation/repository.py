from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from pydantic import JsonValue

from awesome_agent.conversation.models import (
    Thread,
    ThreadEntry,
    ThreadListPage,
    ThreadPage,
    ThreadSummary,
    ThreadView,
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


class ConversationStore(Protocol):
    async def create_thread(self, thread: Thread) -> Thread: ...
    async def set_thread_model(
        self,
        thread_id: str,
        model: str | None,
        *,
        updated_at: datetime,
    ) -> Thread: ...
    async def rename_thread(
        self,
        thread_id: str,
        title: str,
        *,
        updated_at: datetime,
    ) -> Thread: ...
    async def set_thread_thinking(
        self,
        thread_id: str,
        enabled: bool,
        *,
        updated_at: datetime,
    ) -> Thread: ...
    async def set_thread_skill_mode(
        self,
        thread_id: str,
        skill_mode: str,
        *,
        updated_at: datetime,
    ) -> Thread: ...
    async def list_threads(self, workspace_key: str) -> Sequence[Thread]: ...
    async def match_threads(
        self,
        workspace_key: str,
        *,
        prefix: str,
        limit: int,
    ) -> Sequence[Thread]: ...
    async def list_threads_page(
        self,
        workspace_key: str,
        *,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> ThreadListPage: ...
    async def read_thread(self, thread_id: str) -> ThreadView: ...
    async def read_thread_page(
        self,
        thread_id: str,
        *,
        before_sequence: int | None,
        limit: int,
    ) -> ThreadPage: ...
    async def thread_id_for_turn(self, turn_id: str) -> str | None: ...
    async def begin_turn(
        self,
        user_entry: ThreadEntry,
        turn: Turn,
        *,
        automatic_title: str | None,
        updated_at: datetime,
    ) -> Turn: ...
    async def update_in_progress_turn(
        self,
        turn: Turn,
        *,
        expected_context_manifest: tuple[dict[str, JsonValue], ...],
    ) -> Turn: ...
    async def complete_turn(self, assistant_entry: ThreadEntry, turn: Turn) -> Turn: ...
    async def update_terminal_turn(self, turn: Turn) -> Turn: ...
    async def append_direct_command(self, entry: ThreadEntry) -> ThreadEntry: ...
    async def compare_and_swap_summary(
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
