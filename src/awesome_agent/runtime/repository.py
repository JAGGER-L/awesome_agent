from __future__ import annotations

from collections import defaultdict
from typing import Protocol
from uuid import UUID

from awesome_agent.domain.enums import (
    DispatchStatus,
    EventType,
    IntakeReservationStatus,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run, RuntimeEvent, TodoItem
from awesome_agent.repositories.reservations import IntakeReservationStore


class RuntimeRepository(Protocol):
    async def create_run(self, run: Run, leader: Agent) -> None:
        """Persist a new run and its initial Leader."""
        ...

    async def publish_intake(
        self,
        *,
        run: Run,
        leader: Agent,
        events: list[RuntimeEvent],
        reservation_id: UUID,
    ) -> None:
        """Atomically publish a prepared Run and complete its reservation."""
        ...

    async def get_run(self, run_id: UUID) -> Run:
        """Load one run."""
        ...

    async def update_run(self, run: Run) -> None:
        """Persist mutable run state."""
        ...

    async def cancel_run(self, run_id: UUID) -> tuple[Run, RuntimeEvent | None]:
        """Atomically cancel unclaimed work and append its event."""
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
    def __init__(
        self,
        reservations: IntakeReservationStore | None = None,
    ) -> None:
        self._runs: dict[UUID, Run] = {}
        self._agents: dict[UUID, list[Agent]] = defaultdict(list)
        self._todos: dict[UUID, list[TodoItem]] = defaultdict(list)
        self._events: dict[UUID, list[RuntimeEvent]] = defaultdict(list)
        self._reservations = reservations

    async def create_run(self, run: Run, leader: Agent) -> None:
        self._runs[run.id] = run
        self._agents[run.id].append(leader)

    async def publish_intake(
        self,
        *,
        run: Run,
        leader: Agent,
        events: list[RuntimeEvent],
        reservation_id: UUID,
    ) -> None:
        if self._reservations is None:
            raise RuntimeError("Intake reservation store is not configured.")
        reservation = await self._reservations.get(reservation_id)
        published = reservation.model_copy(
            update={"status": IntakeReservationStatus.PUBLISHED}
        )
        self._runs[run.id] = run
        self._agents[run.id].append(leader)
        self._events[run.id].extend(events)
        await self._reservations.update(published)

    async def get_run(self, run_id: UUID) -> Run:
        return self._runs[run_id]

    async def update_run(self, run: Run) -> None:
        self._runs[run.id] = run

    async def cancel_run(self, run_id: UUID) -> tuple[Run, RuntimeEvent | None]:
        from awesome_agent.runtime.dispatch import DispatchConflict

        current = self._runs[run_id]
        if current.dispatch_status in {
            DispatchStatus.CLAIMED,
            DispatchStatus.EXECUTING,
        }:
            raise DispatchConflict("Claimed or executing Runs cannot be cancelled yet.")
        if current.status is RunStatus.CANCELLED:
            return current, None
        run = current.model_copy(
            update={
                "status": RunStatus.CANCELLED,
                "dispatch_status": DispatchStatus.TERMINAL,
            }
        )
        event = RuntimeEvent(
            run_id=run_id,
            sequence=len(self._events[run_id]) + 1,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": RunStatus.CANCELLED.value,
                "dispatch_status": DispatchStatus.TERMINAL.value,
            },
        )
        self._runs[run_id] = run
        self._events[run_id].append(event)
        return run, event

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
