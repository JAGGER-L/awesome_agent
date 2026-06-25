from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.domain.enums import IntakeReservationStatus, RunIntent
from awesome_agent.domain.models import IntakeReservation
from awesome_agent.persistence.models import IntakeReservationRecord
from awesome_agent.repositories.reservations import IntakeReservationStore


class PostgresIntakeReservationStore(IntakeReservationStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(self, reservation: IntakeReservation) -> None:
        async with self._sessions.begin() as session:
            session.add(_to_record(reservation))

    async def get(self, reservation_id: UUID) -> IntakeReservation:
        async with self._sessions() as session:
            record = await session.get(IntakeReservationRecord, reservation_id)
        if record is None:
            raise KeyError(reservation_id)
        return _from_record(record)

    async def update(self, reservation: IntakeReservation) -> None:
        async with self._sessions.begin() as session:
            record = await session.get(IntakeReservationRecord, reservation.id)
            if record is None:
                raise KeyError(reservation.id)
            record.status = reservation.status.value
            record.error = reservation.error
            record.updated_at = reservation.updated_at

    async def list_incomplete(self) -> list[IntakeReservation]:
        terminal = [
            IntakeReservationStatus.PUBLISHED.value,
            IntakeReservationStatus.ROLLED_BACK.value,
        ]
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(IntakeReservationRecord)
                    .where(IntakeReservationRecord.status.not_in(terminal))
                    .order_by(IntakeReservationRecord.created_at)
                )
            )
        return [_from_record(record) for record in records]


def _to_record(reservation: IntakeReservation) -> IntakeReservationRecord:
    return IntakeReservationRecord(
        id=reservation.id,
        run_id=reservation.run_id,
        repository_id=reservation.repository_id,
        base_commit=reservation.base_commit,
        intent=reservation.intent.value,
        workspace_path=str(reservation.workspace_path),
        integration_branch=reservation.integration_branch,
        status=reservation.status.value,
        error=reservation.error,
        created_at=reservation.created_at,
        updated_at=reservation.updated_at,
    )


def _from_record(record: IntakeReservationRecord) -> IntakeReservation:
    return IntakeReservation(
        id=record.id,
        run_id=record.run_id,
        repository_id=record.repository_id,
        base_commit=record.base_commit,
        intent=RunIntent(record.intent),
        workspace_path=Path(record.workspace_path),
        integration_branch=record.integration_branch,
        status=IntakeReservationStatus(record.status),
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
