from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import IntakeReservationStatus, RunIntent
from awesome_agent.domain.models import IntakeReservation
from awesome_agent.repositories.reservations import (
    InMemoryIntakeReservationStore,
)


@pytest.mark.asyncio
async def test_reservation_store_lists_only_incomplete_records(
    tmp_path: Path,
) -> None:
    store = InMemoryIntakeReservationStore()
    reservation = IntakeReservation(
        run_id=uuid4(),
        repository_id=uuid4(),
        base_commit="a" * 40,
        intent=RunIntent.MODIFYING,
        workspace_path=tmp_path / "workspace",
        integration_branch=f"awesome-agent/run/{uuid4()}",
    )
    await store.create(reservation)

    assert await store.list_incomplete() == [reservation]

    published = reservation.model_copy(
        update={"status": IntakeReservationStatus.PUBLISHED}
    )
    await store.update(published)

    assert await store.list_incomplete() == []
