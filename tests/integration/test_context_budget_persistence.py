from __future__ import annotations

import os
from uuid import uuid4

import pytest

from awesome_agent.persistence.budget import (
    ContextCompactionRecord,
    PostgresBudgetRepository,
    RunBudgetLedgerRecord,
)
from awesome_agent.persistence.database import create_engine, create_session_factory

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_budget_ledger_round_trips_through_postgres() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    repository = PostgresBudgetRepository(sessions)
    run_id = uuid4()
    ledger = RunBudgetLedgerRecord(
        run_id=run_id,
        total_input_tokens=10,
        total_output_tokens=20,
        total_reasoning_tokens=5,
        active_seconds=30,
        model_call_count=2,
        threshold_status="within_budget",
    )

    saved = await repository.upsert_ledger(ledger)
    loaded = await repository.get_ledger(run_id)

    assert loaded == saved
    await engine.dispose()


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_context_compaction_records_artifact_refs() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    repository = PostgresBudgetRepository(sessions)
    compaction = ContextCompactionRecord(
        run_id=uuid4(),
        agent_id=uuid4(),
        graph_name="solo-readonly",
        before_estimated_tokens=50_000,
        after_estimated_tokens=10_000,
        summary="Inspected repository files.",
        artifact_refs=[uuid4()],
    )

    saved = await repository.record_compaction(compaction)

    assert saved.id == compaction.id
    assert saved.artifact_refs == compaction.artifact_refs
    assert not hasattr(saved, "graph_version")
    await engine.dispose()
