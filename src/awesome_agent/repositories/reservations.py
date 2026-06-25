from __future__ import annotations

from typing import Protocol
from uuid import UUID

from awesome_agent.domain.enums import IntakeReservationStatus
from awesome_agent.domain.models import IntakeReservation


class IntakeReservationStore(Protocol):
    async def create(self, reservation: IntakeReservation) -> None:
        """Persist a private intake reservation before Git side effects."""
        ...

    async def get(self, reservation_id: UUID) -> IntakeReservation:
        """Load one intake reservation."""
        ...

    async def update(self, reservation: IntakeReservation) -> None:
        """Persist reservation lifecycle changes."""
        ...

    async def list_incomplete(self) -> list[IntakeReservation]:
        """Load reservations that still require publication or rollback."""
        ...


class InMemoryIntakeReservationStore(IntakeReservationStore):
    def __init__(self) -> None:
        self._reservations: dict[UUID, IntakeReservation] = {}

    async def create(self, reservation: IntakeReservation) -> None:
        self._reservations[reservation.id] = reservation

    async def get(self, reservation_id: UUID) -> IntakeReservation:
        return self._reservations[reservation_id]

    async def update(self, reservation: IntakeReservation) -> None:
        self._reservations[reservation.id] = reservation

    async def list_incomplete(self) -> list[IntakeReservation]:
        terminal = {
            IntakeReservationStatus.PUBLISHED,
            IntakeReservationStatus.ROLLED_BACK,
        }
        return [
            reservation
            for reservation in self._reservations.values()
            if reservation.status not in terminal
        ]
