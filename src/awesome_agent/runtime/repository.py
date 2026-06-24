from __future__ import annotations

from collections import defaultdict
from typing import Protocol
from uuid import UUID

from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import Agent, Run, RuntimeEvent, TodoItem


class RuntimeRepository(Protocol):
    async def create_run(self, run: Run, leader: Agent) -> None:
        """Persist a new run and its initial Leader."""
        ...

    async def get_run(self, run_id: UUID) -> Run:
        """Load one run."""
        ...

    async def update_run(self, run: Run) -> None:
        """Persist mutable run state."""
        ...

    async def list_agents(self, run_id: UUID) -> list[Agent]:
        """Load all agents in a run."""
        ...

    async def list_todos(self, run_id: UUID) -> list[TodoItem]:
        """Load all tasks in a run."""
        ...

    async def append_event(
        self,
        *,
        run_id: UUID,
        event_type: EventType,
        payload: dict[str, object],
        agent_id: UUID | None = None,
    ) -> RuntimeEvent:
        """Append and return the next sequenced event."""
        ...

    async def list_events(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> list[RuntimeEvent]:
        """Load ordered event history."""
        ...


class InMemoryRuntimeRepository(RuntimeRepository):
    def __init__(self) -> None:
        self._runs: dict[UUID, Run] = {}
        self._agents: dict[UUID, list[Agent]] = defaultdict(list)
        self._todos: dict[UUID, list[TodoItem]] = defaultdict(list)
        self._events: dict[UUID, list[RuntimeEvent]] = defaultdict(list)

    async def create_run(self, run: Run, leader: Agent) -> None:
        self._runs[run.id] = run
        self._agents[run.id].append(leader)

    async def get_run(self, run_id: UUID) -> Run:
        return self._runs[run_id]

    async def update_run(self, run: Run) -> None:
        self._runs[run.id] = run

    async def list_agents(self, run_id: UUID) -> list[Agent]:
        return list(self._agents[run_id])

    async def list_todos(self, run_id: UUID) -> list[TodoItem]:
        return list(self._todos[run_id])

    async def append_event(
        self,
        *,
        run_id: UUID,
        event_type: EventType,
        payload: dict[str, object],
        agent_id: UUID | None = None,
    ) -> RuntimeEvent:
        event = RuntimeEvent(
            run_id=run_id,
            sequence=len(self._events[run_id]) + 1,
            event_type=event_type,
            payload=payload,
            agent_id=agent_id,
        )
        self._events[run_id].append(event)
        return event

    async def list_events(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> list[RuntimeEvent]:
        return [
            event for event in self._events[run_id] if event.sequence > after_sequence
        ]
