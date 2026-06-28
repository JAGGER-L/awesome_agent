from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.observability.repository import (
    DurableMetric,
    DurableModelCall,
    DurableSpan,
    PostgresObservabilityRepository,
)
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.models import RunRecord

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_observability_records_round_trip_through_postgres() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    repository = PostgresObservabilityRepository(sessions)
    run_id = await _insert_run(sessions)
    started = datetime.now(UTC)
    ended = started + timedelta(milliseconds=31)

    span = await repository.record_span(
        DurableSpan(
            run_id=run_id,
            trace_id=run_id.hex,
            span_id="0000000000000001",
            parent_span_id=None,
            name="run.execute",
            category="run",
            status="completed",
            started_at=started,
            ended_at=ended,
            duration_ms=31,
            attributes={"graph": "solo-readonly"},
        )
    )
    metric = await repository.record_metric(
        DurableMetric(
            run_id=run_id,
            name="run.duration_ms",
            value=31,
            unit="ms",
            attributes={"status": "completed"},
        )
    )
    model_call = await repository.record_model_call(
        DurableModelCall(
            run_id=run_id,
            agent_id=None,
            turn=1,
            provider="deepseek",
            model="deepseek-v4-flash",
            status="completed",
            stop_reason="completed",
            input_tokens=10,
            output_tokens=20,
            latency_ms=31,
            trace_id=run_id.hex,
            span_id="0000000000000002",
        )
    )

    assert await repository.list_spans_for_run(run_id) == [span]
    assert await repository.list_metrics_for_run(run_id) == [metric]
    assert await repository.list_model_calls_for_run(run_id) == [model_call]

    await _delete_run(sessions, run_id)
    await engine.dispose()


async def _insert_run(sessions: async_sessionmaker[AsyncSession]) -> UUID:
    run_id = uuid4()
    async with sessions.begin() as session:
        await session.execute(
            text(
                """
                INSERT INTO runs (
                    id, goal, mode, status, intent, execution_kind,
                    dispatch_status, available_at, depth, fencing_token,
                    attempt, legacy,
                    created_at, updated_at
                )
                VALUES (
                    :id, 'observability fixture', 'solo', 'created', 'read_only',
                    'coding', 'queued', clock_timestamp(), 0, 0, 0, false,
                    clock_timestamp(), clock_timestamp()
                )
                """
            ),
            {"id": run_id},
        )
    return run_id


async def _delete_run(
    sessions: async_sessionmaker[AsyncSession],
    run_id: UUID,
) -> None:
    async with sessions.begin() as session:
        await session.execute(delete(RunRecord).where(RunRecord.id == run_id))
