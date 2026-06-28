from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import RunMode
from awesome_agent.domain.models import Run
from awesome_agent.persistence.budget import (
    InMemoryBudgetRepository,
    RunBudgetLedgerRecord,
)
from awesome_agent.runtime.budget import BudgetDecision, BudgetPolicy
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
)
from awesome_agent.runtime.team_budget import (
    build_team_attribution,
    ensure_team_budget,
    evaluate_team_budget,
    load_team_budget_snapshot,
)


@pytest.mark.asyncio
async def test_team_budget_snapshot_aggregates_root_and_descendants() -> None:
    runtime = InMemoryRuntimeRepository()
    budgets = InMemoryBudgetRepository()
    root = Run(goal="root", mode=RunMode.TEAM)
    child = Run(
        goal="child",
        mode=RunMode.TEAM,
        parent_run_id=root.id,
        root_run_id=root.id,
        depth=1,
    )
    grandchild = Run(
        goal="grandchild",
        mode=RunMode.TEAM,
        parent_run_id=child.id,
        root_run_id=root.id,
        depth=2,
    )
    await runtime.create_run(root, _agent_stub(root.id))
    await runtime.create_run(child, _agent_stub(child.id))
    await runtime.create_run(grandchild, _agent_stub(grandchild.id))
    now = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
    await budgets.upsert_ledger(
        RunBudgetLedgerRecord(
            run_id=root.id,
            total_input_tokens=10,
            total_output_tokens=5,
            total_reasoning_tokens=2,
            active_seconds=1,
            model_call_count=1,
        )
    )
    await budgets.upsert_ledger(
        RunBudgetLedgerRecord(
            run_id=child.id,
            total_input_tokens=20,
            total_output_tokens=7,
            total_reasoning_tokens=3,
            active_seconds=2,
            model_call_count=2,
            active_window_started_at=now - timedelta(seconds=5),
        )
    )
    await budgets.upsert_ledger(
        RunBudgetLedgerRecord(
            run_id=grandchild.id,
            total_input_tokens=30,
            total_output_tokens=8,
            total_reasoning_tokens=4,
            active_seconds=3,
            model_call_count=3,
        )
    )

    snapshot = await load_team_budget_snapshot(
        root_run_id=root.id,
        repository=runtime,
        budget_repository=budgets,
        now=now,
    )

    assert snapshot.run_ids == [root.id, child.id, grandchild.id]
    assert snapshot.total_input_tokens == 60
    assert snapshot.total_output_tokens == 20
    assert snapshot.total_reasoning_tokens == 9
    assert snapshot.active_seconds == 11
    assert snapshot.model_call_count == 6


@pytest.mark.asyncio
async def test_team_budget_evaluation_uses_root_aggregate() -> None:
    runtime = InMemoryRuntimeRepository()
    budgets = InMemoryBudgetRepository()
    root = Run(goal="root", mode=RunMode.TEAM)
    child = Run(
        goal="child",
        mode=RunMode.TEAM,
        parent_run_id=root.id,
        root_run_id=root.id,
        depth=1,
    )
    await runtime.create_run(root, _agent_stub(root.id))
    await runtime.create_run(child, _agent_stub(child.id))
    await budgets.upsert_ledger(
        RunBudgetLedgerRecord(
            run_id=root.id,
            total_input_tokens=40,
            total_output_tokens=20,
        )
    )
    await budgets.upsert_ledger(
        RunBudgetLedgerRecord(
            run_id=child.id,
            total_input_tokens=30,
            total_output_tokens=10,
        )
    )

    decision, snapshot = await evaluate_team_budget(
        root_run_id=root.id,
        repository=runtime,
        budget_repository=budgets,
        policy=BudgetPolicy(
            soft_context_tokens=1000,
            hard_context_tokens=2000,
            recent_context_tokens=800,
            max_total_tokens_per_run=120,
            max_reasoning_tokens_per_run=1000,
            max_active_seconds_per_run=3600,
        ),
        estimated_prompt_tokens=25,
        now=datetime.now(UTC),
    )

    assert snapshot.total_tokens == 100
    assert decision is BudgetDecision.EXHAUSTED


@pytest.mark.asyncio
async def test_ensure_team_budget_emits_exhaustion_with_attribution() -> None:
    runtime = InMemoryRuntimeRepository()
    budgets = InMemoryBudgetRepository()
    root = Run(goal="root", mode=RunMode.TEAM)
    child = Run(
        goal="child",
        mode=RunMode.TEAM,
        parent_run_id=root.id,
        root_run_id=root.id,
        depth=1,
        child_role="teammate",
    )
    await runtime.create_run(root, _agent_stub(root.id))
    await runtime.create_run(child, _agent_stub(child.id))
    await budgets.upsert_ledger(
        RunBudgetLedgerRecord(
            run_id=root.id,
            total_input_tokens=100,
            total_output_tokens=50,
        )
    )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    from awesome_agent.runtime.dispatch import PermanentExecutionError

    with pytest.raises(PermanentExecutionError, match="team_budget_exhausted"):
        await ensure_team_budget(
            run=child,
            repository=runtime,
            budget_repository=budgets,
            policy=BudgetPolicy(
                soft_context_tokens=1000,
                hard_context_tokens=2000,
                recent_context_tokens=800,
                max_total_tokens_per_run=120,
                max_reasoning_tokens_per_run=1000,
                max_active_seconds_per_run=3600,
            ),
            estimated_prompt_tokens=1,
            now=datetime.now(UTC),
            event_sink=emit,
            agent_id=_agent_stub(child.id).id,
        )

    assert events[0][1]["scope"] == "team_root"
    assert events[0][1]["root_run_id"] == str(root.id)
    assert events[0][1]["child_run_id"] == str(child.id)
    assert events[0][1]["team_total_input_tokens"] == 100


def test_build_team_attribution_includes_lineage_assignment_and_agent() -> None:
    run = Run(
        goal="child",
        mode=RunMode.TEAM,
        parent_run_id=uuid4(),
        root_run_id=uuid4(),
        depth=1,
        child_role="teammate",
    )
    assignment = TeamAssignment(
        root_run_id=run.root_run_id or run.id,
        parent_run_id=run.parent_run_id or run.id,
        child_run_id=run.id,
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="backend",
        graph_name="team-role",
        graph_version=1,
        goal="Implement backend.",
    )
    agent_id = uuid4()

    payload = build_team_attribution(
        run=run,
        assignment=assignment,
        agent_id=agent_id,
    )

    assert payload == {
        "root_run_id": str(run.root_run_id),
        "parent_run_id": str(run.parent_run_id),
        "child_run_id": str(run.id),
        "depth": 1,
        "child_role": "teammate",
        "assignment_id": str(assignment.id),
        "assignment_kind": "teammate",
        "role_profile": "backend",
        "agent_id": str(agent_id),
    }


def _agent_stub(run_id):
    from awesome_agent.domain.enums import AgentKind
    from awesome_agent.domain.models import Agent

    return Agent(run_id=run_id, kind=AgentKind.LEADER, profile="leader", model="fake")
