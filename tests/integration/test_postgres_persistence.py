from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import RuntimeEvent
from awesome_agent.persistence.database import (
    create_engine,
    create_session_factory,
    session_scope,
)
from awesome_agent.persistence.events import EventRepository

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_event_repository_round_trip() -> None:
    database_url = os.environ["AWESOME_AGENT_TEST_DATABASE_URL"]
    engine = create_engine(database_url)
    factory = create_session_factory(engine)
    run_id = uuid4()

    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO runs (id, goal, mode, status, created_at, updated_at)
                VALUES (:id, 'test', 'solo', 'created', now(), now())
                """
            ),
            {"id": run_id},
        )

    event = RuntimeEvent(
        run_id=run_id,
        sequence=1,
        event_type=EventType.RUN_CREATED,
        payload={"goal": "test"},
    )
    async with session_scope(factory) as session:
        repository = EventRepository(session)
        await repository.append(event)

    async with session_scope(factory) as session:
        repository = EventRepository(session)
        events = await repository.list_for_run(run_id)

    assert events == [event]
    await engine.dispose()
