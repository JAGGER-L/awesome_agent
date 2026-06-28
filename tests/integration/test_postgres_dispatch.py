from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.domain.enums import DispatchStatus, EventType, RunStatus
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.dispatch import PostgresRunDispatcher
from awesome_agent.persistence.models import RunRecord
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.runtime.dispatch import DispatchConflict, LeaseLost

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_two_workers_claim_one_run_once() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    await _delete_dispatch_fixtures(sessions)
    run_id = await _insert_queued_run(sessions)
    dispatcher = PostgresRunDispatcher(sessions)

    first, second = await asyncio.gather(
        dispatcher.claim_next(
            worker_id=uuid4(),
            worker_name="worker-a",
            lease_duration=timedelta(seconds=60),
            max_attempts=3,
            runtime_routes=_fixture_graphs(),
        ),
        dispatcher.claim_next(
            worker_id=uuid4(),
            worker_name="worker-b",
            lease_duration=timedelta(seconds=60),
            max_attempts=3,
            runtime_routes=_fixture_graphs(),
        ),
    )

    leases = [lease for lease in (first, second) if lease is not None]
    assert len(leases) == 1
    assert leases[0].run_id == run_id
    assert leases[0].attempt == 1
    assert leases[0].fencing_token == 1
    await _delete_run(sessions, run_id)
    await engine.dispose()


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_heartbeat_and_fencing_reject_stale_owner() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    await _delete_dispatch_fixtures(sessions)
    run_id = await _insert_queued_run(sessions)
    dispatcher = PostgresRunDispatcher(sessions)
    lease = await dispatcher.claim_next(
        worker_id=uuid4(),
        worker_name="worker-a",
        lease_duration=timedelta(seconds=60),
        max_attempts=3,
        runtime_routes=_fixture_graphs(),
    )
    assert lease is not None

    renewed = await dispatcher.heartbeat(
        lease,
        lease_duration=timedelta(seconds=60),
    )
    assert renewed.lease_expires_at > lease.lease_expires_at

    await dispatcher.release_to_queue(
        renewed,
        reason="handoff",
        max_attempts=3,
    )
    replacement = await dispatcher.claim_next(
        worker_id=uuid4(),
        worker_name="worker-b",
        lease_duration=timedelta(seconds=60),
        max_attempts=3,
        runtime_routes=_fixture_graphs(),
    )
    assert replacement is not None
    assert replacement.fencing_token == 2
    with pytest.raises(LeaseLost):
        await dispatcher.append_fenced_event(
            renewed,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={"status": "invalid"},
        )

    events = await PostgresRuntimeRepository(sessions).list_events(run_id)
    assert [event.event_type for event in events] == [
        EventType.DISPATCH_CLAIMED,
        EventType.DISPATCH_RELEASED,
        EventType.DISPATCH_CLAIMED,
    ]
    await _delete_run(sessions, run_id)
    await engine.dispose()


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_retry_delay_and_expired_lease_recovery() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    await _delete_dispatch_fixtures(sessions)
    run_id = await _insert_queued_run(sessions)
    dispatcher = PostgresRunDispatcher(sessions)
    lease = await dispatcher.claim_next(
        worker_id=uuid4(),
        worker_name="worker-a",
        lease_duration=timedelta(seconds=60),
        max_attempts=3,
        runtime_routes=_fixture_graphs(),
    )
    assert lease is not None
    await dispatcher.release_for_retry(
        lease,
        delay=timedelta(minutes=5),
        reason="temporary failure",
        max_attempts=3,
        error="provider unavailable",
    )
    assert (
        await dispatcher.claim_next(
            worker_id=uuid4(),
            worker_name="too-early",
            lease_duration=timedelta(seconds=60),
            max_attempts=3,
            runtime_routes=_fixture_graphs(),
        )
        is None
    )

    async with sessions.begin() as session:
        await session.execute(
            text(
                """
                UPDATE runs
                SET available_at = clock_timestamp() - interval '1 second'
                WHERE id = :run_id
                """
            ),
            {"run_id": run_id},
        )
    replacement = await dispatcher.claim_next(
        worker_id=uuid4(),
        worker_name="worker-b",
        lease_duration=timedelta(seconds=60),
        max_attempts=3,
        runtime_routes=_fixture_graphs(),
    )
    assert replacement is not None
    async with sessions.begin() as session:
        await session.execute(
            text(
                """
                UPDATE runs
                SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE id = :run_id
                """
            ),
            {"run_id": run_id},
        )

    assert await dispatcher.recover_expired(max_attempts=3) >= 1
    restored = await PostgresRuntimeRepository(sessions).get_run(run_id)
    assert restored.dispatch_status is DispatchStatus.QUEUED
    assert restored.status is RunStatus.CREATED

    final = await dispatcher.claim_next(
        worker_id=uuid4(),
        worker_name="worker-c",
        lease_duration=timedelta(seconds=60),
        max_attempts=3,
        runtime_routes=_fixture_graphs(),
    )
    assert final is not None and final.attempt == 3
    async with sessions.begin() as session:
        await session.execute(
            text(
                """
                UPDATE runs
                SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE id = :run_id
                """
            ),
            {"run_id": run_id},
        )
    assert await dispatcher.recover_expired(max_attempts=3) >= 1
    terminal = await PostgresRuntimeRepository(sessions).get_run(run_id)
    assert terminal.status is RunStatus.RECOVERY_REQUIRED
    assert terminal.dispatch_status is DispatchStatus.TERMINAL
    await _delete_run(sessions, run_id)
    await engine.dispose()


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_cancellation_is_atomic_and_rejects_claimed_run() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    await _delete_dispatch_fixtures(sessions)
    runtime = PostgresRuntimeRepository(sessions)
    dispatcher = PostgresRunDispatcher(sessions)
    queued_id = await _insert_queued_run(sessions)

    cancelled, event = await runtime.cancel_run(queued_id)
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.dispatch_status is DispatchStatus.TERMINAL
    assert event is not None
    repeated, repeated_event = await runtime.cancel_run(queued_id)
    assert repeated.status is RunStatus.CANCELLED
    assert repeated_event is None

    claimed_id = await _insert_queued_run(sessions)
    lease = await dispatcher.claim_next(
        worker_id=uuid4(),
        worker_name="worker",
        lease_duration=timedelta(seconds=60),
        max_attempts=3,
        runtime_routes=_fixture_graphs(),
    )
    assert lease is not None and lease.run_id == claimed_id
    with pytest.raises(DispatchConflict):
        await runtime.cancel_run(claimed_id)

    await _delete_run(sessions, queued_id)
    await _delete_run(sessions, claimed_id)
    await engine.dispose()


async def _insert_queued_run(
    sessions: async_sessionmaker[AsyncSession],
) -> UUID:
    run_id = uuid4()
    async with sessions.begin() as session:
        await session.execute(
            text(
                """
                INSERT INTO runs (
                    id, goal, mode, status, intent, execution_kind,
                    dispatch_status, runtime_route, available_at, fencing_token,
                    attempt, depth, legacy,
                    created_at, updated_at
                )
                VALUES (
                    :id, 'dispatch fixture', 'solo', 'created', 'read_only',
                    'coding', 'queued', 'dispatch-fixture', clock_timestamp(),
                    0, 0, 0, false, clock_timestamp(), clock_timestamp()
                )
                """
            ),
            {"id": run_id},
        )
    return run_id


def _fixture_graphs() -> frozenset[str]:
    return frozenset({"dispatch-fixture"})


async def _delete_dispatch_fixtures(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as session:
        await session.execute(
            delete(RunRecord).where(RunRecord.goal == "dispatch fixture")
        )


async def _delete_run(
    sessions: async_sessionmaker[AsyncSession],
    run_id: UUID,
) -> None:
    async with sessions.begin() as session:
        await session.execute(delete(RunRecord).where(RunRecord.id == run_id))
