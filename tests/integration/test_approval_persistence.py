from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from awesome_agent.domain.enums import ApprovalStatus
from awesome_agent.persistence.approvals import (
    ApprovalExpired,
    DurableApproval,
    PostgresApprovalRepository,
)
from awesome_agent.persistence.database import (
    create_engine,
    create_session_factory,
)
from awesome_agent.persistence.tool_invocations import (
    DurableToolInvocation,
    PostgresToolInvocationRepository,
)

pytestmark = pytest.mark.integration


_RUN_SQL = """
INSERT INTO runs (
    id, goal, mode, status, intent, execution_kind, dispatch_status, available_at,
    depth, fencing_token, attempt, legacy, created_at, updated_at
) VALUES (
    :id, 'approval fixture', 'solo', 'created', 'modifying', 'coding', 'queued',
    now(), 0, 0, 0, false, now(), now()
)
"""

_AGENT_SQL = """
INSERT INTO agents (
    id, run_id, kind, profile, model, status, revision, created_at, updated_at
) VALUES (
    :id, :run_id, 'leader', 'leader', 'fake-model', 'ready', 1, now(), now()
)
"""


async def _seed_run_and_tool_invocation(
    engine: AsyncEngine,
    run_id: UUID,
    agent_id: UUID,
    tool_invocation_id: UUID,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(_RUN_SQL), {"id": run_id})
        await connection.execute(text(_AGENT_SQL), {"id": agent_id, "run_id": run_id})
        await connection.execute(
            text(
                """
                INSERT INTO tool_invocations (
                    id, run_id, agent_id, tool_name, tool_version, status,
                    idempotency_key, arguments_hash, risk_level,
                    path_refs, preimage_hashes, expected_postimage_hashes,
                    result_is_error, artifact_refs, created_at, updated_at
                ) VALUES (
                    :id, :run_id, :agent_id, 'shell.execute', '1', 'started',
                    :key, :hash, 'medium',
                    '[]', '{}', '{}',
                    false, '[]', now(), now()
                )
                """
            ),
            {
                "id": tool_invocation_id,
                "run_id": run_id,
                "agent_id": agent_id,
                "key": f"key-{tool_invocation_id}",
                "hash": "x" * 64,
            },
        )


async def _cleanup_run(engine: AsyncEngine, run_id: UUID) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM approvals WHERE run_id = :id"), {"id": run_id}
        )
        await connection.execute(
            text("DELETE FROM tool_invocations WHERE run_id = :id"),
            {"id": run_id},
        )
        await connection.execute(
            text("DELETE FROM agents WHERE run_id = :id"), {"id": run_id}
        )
        await connection.execute(
            text("DELETE FROM runs WHERE id = :id"), {"id": run_id}
        )


def _approval(
    *,
    run_id: UUID,
    agent_id: UUID,
    tool_invocation_id: UUID,
    expires_at: datetime | None = None,
    tool_call_id: str = "call-1",
) -> DurableApproval:
    return DurableApproval(
        run_id=run_id,
        agent_id=agent_id,
        tool_invocation_id=tool_invocation_id,
        tool_call_id=tool_call_id,
        tool_name="shell.execute",
        tool_version="1",
        canonical_arguments={"argv": ["python", "script.py"]},
        arguments_hash="a" * 64,
        workspace_path="E:/workspace",
        workspace_fingerprint="b" * 64,
        capabilities=["shell:execute"],
        risk_level="medium",
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=60),
    )


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_postgres_approval_repository_round_trip_and_decide() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    run_id = uuid4()
    agent_id = uuid4()
    tool_invocation_id = uuid4()
    await _seed_run_and_tool_invocation(engine, run_id, agent_id, tool_invocation_id)
    try:
        repository = PostgresApprovalRepository(sessions)
        approval = await repository.upsert(
            _approval(
                run_id=run_id,
                agent_id=agent_id,
                tool_invocation_id=tool_invocation_id,
            )
        )

        loaded = await repository.get(approval.id)
        assert loaded == approval
        assert loaded.status is ApprovalStatus.PENDING

        found_by_call = await repository.get_by_call(run_id, "call-1")
        assert found_by_call is not None
        assert found_by_call.id == approval.id

        missing = await repository.get_by_call(run_id, "no-such-call")
        assert missing is None

        now = datetime.now(UTC)
        decided = await repository.decide(
            approval.id,
            approved=True,
            decided_by="cli",
            reason="approved by test",
            now=now,
        )
        assert decided.status is ApprovalStatus.APPROVED
        assert decided.decided_by == "cli"

        repeated = await repository.decide(
            approval.id,
            approved=False,
            decided_by="cli",
            reason="ignored",
            now=now + timedelta(seconds=1),
        )
        assert repeated.status is ApprovalStatus.APPROVED

        listed = await repository.list_for_run(run_id)
        assert [item.id for item in listed] == [approval.id]
    finally:
        await _cleanup_run(engine, run_id)
        await engine.dispose()


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_postgres_approval_repository_expires_and_rejects_after_expiry() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    run_id = uuid4()
    agent_id = uuid4()
    tool_invocation_id = uuid4()
    await _seed_run_and_tool_invocation(engine, run_id, agent_id, tool_invocation_id)
    try:
        repository = PostgresApprovalRepository(sessions)
        now = datetime.now(UTC)
        expired = await repository.upsert(
            _approval(
                run_id=run_id,
                agent_id=agent_id,
                tool_invocation_id=tool_invocation_id,
                expires_at=now - timedelta(seconds=1),
            )
        )

        expired_items = await repository.expire_expired(now)
        expired_ids = {item.id for item in expired_items}
        assert expired.id in expired_ids
        assert (await repository.get(expired.id)).status is ApprovalStatus.EXPIRED

        # decide on an already-expired approval is idempotent (returns the
        # expired record) rather than raising, because the status is no longer
        # pending when the CAS guard runs.
        repeated = await repository.decide(
            expired.id,
            approved=True,
            decided_by="cli",
            reason=None,
            now=now,
        )
        assert repeated.status is ApprovalStatus.EXPIRED

        # A pending approval whose deadline has passed but has not yet been
        # scanned raises ApprovalExpired on decide. Because the Postgres
        # repository writes the expired flip inside the same transaction that
        # raises, the rollback leaves the row pending; the background
        # expire_expired scan is what persists the expired status.
        pending_late = await repository.upsert(
            _approval(
                run_id=run_id,
                agent_id=agent_id,
                tool_invocation_id=tool_invocation_id,
                expires_at=now - timedelta(seconds=1),
                tool_call_id="call-late",
            )
        )
        with pytest.raises(ApprovalExpired):
            await repository.decide(
                pending_late.id,
                approved=True,
                decided_by="cli",
                reason=None,
                now=now,
            )
        assert (await repository.get(pending_late.id)).status is ApprovalStatus.PENDING

        # The background scan is what persists the expired status.
        expired_late = await repository.expire_expired(now)
        assert pending_late.id in {item.id for item in expired_late}
        assert (await repository.get(pending_late.id)).status is ApprovalStatus.EXPIRED
    finally:
        await _cleanup_run(engine, run_id)
        await engine.dispose()


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_postgres_tool_invocation_repository_round_trip() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    run_id = uuid4()
    agent_id = uuid4()
    invocation_id = uuid4()
    await _seed_run_and_tool_invocation(engine, run_id, agent_id, invocation_id)
    try:
        repository = PostgresToolInvocationRepository(sessions)
        invocation = DurableToolInvocation(
            id=invocation_id,
            run_id=run_id,
            agent_id=agent_id,
            tool_name="shell.execute",
            tool_version="1",
            status="started",
            idempotency_key=f"key-{invocation_id}",
            arguments_hash="x" * 64,
            risk_level="medium",
        )
        await repository.upsert(invocation)

        loaded = await repository.get(invocation_id)
        assert loaded.tool_name == "shell.execute"
        assert loaded.status == "started"

        found = await repository.get_by_idempotency_key(run_id, f"key-{invocation_id}")
        assert found is not None
        assert found.id == invocation_id

        missing = await repository.get_by_idempotency_key(run_id, "no-such-key")
        assert missing is None

        listed = await repository.list_for_run(run_id)
        assert [item.id for item in listed] == [invocation_id]
    finally:
        await _cleanup_run(engine, run_id)
        await engine.dispose()
