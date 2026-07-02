from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from awesome_agent.domain.threads import Thread


class InMemoryThreadRepository:
    def __init__(self) -> None:
        self._threads: dict[UUID, Thread] = {}

    async def create(self, *, title: str) -> Thread:
        thread = Thread(title=title)
        self._threads[thread.id] = thread
        return thread

    async def get(self, thread_id: UUID) -> Thread:
        try:
            return self._threads[thread_id]
        except KeyError as error:
            raise KeyError(f"Thread not found: {thread_id}") from error

    async def list(self) -> Sequence[Thread]:
        return sorted(
            self._threads.values(),
            key=lambda thread: thread.updated_at,
            reverse=True,
        )
