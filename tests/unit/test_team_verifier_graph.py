import pytest

from awesome_agent.domain.enums import AgentKind, RunIntent, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.team import InMemoryTeamRepository
from awesome_agent.runtime.graphs import TEAM_VERIFIER_GRAPH
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamChildResult,
)
from awesome_agent.runtime.team_mailbox import MailboxRoute
from awesome_agent.runtime.team_verifier_graph import (
    TeamVerifierGraph,
    verifier_external_retry_budget,
    verifier_model_rejection_budget,
)


@pytest.mark.asyncio
async def test_verifier_passes_aggregated_child_results() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamVerifierGraph(team_repository=teams)
    parent = Run(goal="parent", mode=RunMode.TEAM)
    verifier, agent, assignment = _verifier_run(parent)
    await runtime.create_run(
        parent,
        Agent(
            run_id=parent.id,
            kind=AgentKind.LEADER,
            profile="leader",
            model="fake",
        ),
    )
    await runtime.create_run(verifier, agent)
    await teams.create_assignment(assignment)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=assignment.id,
            child_run_id=Run(goal="teammate").id,
            parent_run_id=parent.id,
            root_run_id=parent.id,
            status="completed",
            summary="done",
            patch_aggregated=True,
        )
    )

    state, recovered = await graph.execute(verifier, agent, repository=runtime)

    result = next(
        item
        for item in await teams.list_child_results(parent.id)
        if item.child_run_id == verifier.id
    )
    mailbox = await teams.list_mailbox_messages(parent.id)
    assert not recovered
    assert state["phase"] == "passed"
    assert result.child_run_id == verifier.id
    assert result.status == "completed"
    assert mailbox[-1].route is MailboxRoute.VERIFIER_TO_LEADER


@pytest.mark.asyncio
async def test_verifier_rejects_unaggregated_patch_result() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamVerifierGraph(team_repository=teams)
    parent = Run(goal="parent", mode=RunMode.TEAM)
    verifier, agent, assignment = _verifier_run(parent)
    await runtime.create_run(
        parent,
        Agent(
            run_id=parent.id,
            kind=AgentKind.LEADER,
            profile="leader",
            model="fake",
        ),
    )
    await runtime.create_run(verifier, agent)
    await teams.create_assignment(assignment)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=assignment.id,
            child_run_id=Run(goal="teammate").id,
            parent_run_id=parent.id,
            root_run_id=parent.id,
            status="completed",
            summary="patch not aggregated",
            patch_artifact_id=Run(goal="artifact").id,
            patch_aggregated=False,
        )
    )

    state, _ = await graph.execute(verifier, agent, repository=runtime)

    result = next(
        item
        for item in await teams.list_child_results(parent.id)
        if item.child_run_id == verifier.id
    )
    mailbox = await teams.list_mailbox_messages(parent.id)
    assert state["phase"] == "rejected"
    assert result.status == "failed"
    assert result.failure_kind == "model_output_failure"
    assert mailbox[-1].requires_response


def test_verifier_retry_budgets_are_explicit() -> None:
    assert verifier_model_rejection_budget() == 10
    assert verifier_external_retry_budget() == 1


def _verifier_run(parent: Run) -> tuple[Run, Agent, TeamAssignment]:
    run = Run(
        goal="verify",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        parent_run_id=parent.id,
        root_run_id=parent.id,
        depth=1,
        child_role="verifier",
        graph_name=TEAM_VERIFIER_GRAPH,
    )
    agent = Agent(
        run_id=run.id,
        kind=AgentKind.VERIFIER,
        profile="verifier",
        model="fake",
    )
    assignment = TeamAssignment(
        root_run_id=parent.id,
        parent_run_id=parent.id,
        child_run_id=run.id,
        kind=TeamAssignmentKind.VERIFIER,
        role_profile="verifier",
        graph_name=TEAM_VERIFIER_GRAPH,
        goal="verify",
    )
    return run, agent, assignment
