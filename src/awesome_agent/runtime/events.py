from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncGenerator
from uuid import UUID

from awesome_agent.domain.models import RuntimeEvent


class EventStream:
    def __init__(self) -> None:
        self._events: dict[UUID, list[RuntimeEvent]] = defaultdict(list)
        self._subscribers: dict[UUID, set[asyncio.Queue[RuntimeEvent]]] = defaultdict(
            set
        )

    async def publish(self, event: RuntimeEvent) -> None:
        self._events[event.run_id].append(event)
        for queue in list(self._subscribers[event.run_id]):
            await queue.put(event)

    def history(self, run_id: UUID, *, after_sequence: int = 0) -> list[RuntimeEvent]:
        return [
            event for event in self._events[run_id] if event.sequence > after_sequence
        ]

    async def subscribe(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> AsyncGenerator[RuntimeEvent]:
        for event in self.history(run_id, after_sequence=after_sequence):
            yield event

        queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        self._subscribers[run_id].add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers[run_id].discard(queue)
