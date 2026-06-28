from pathlib import Path

import pytest

from awesome_agent.artifacts.repository import InMemoryArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import AgentKind, RunIntent, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.budget import InMemoryBudgetRepository
from awesome_agent.persistence.team import InMemoryTeamRepository
from awesome_agent.runtime.dispatch import ChildRunWait
from awesome_agent.runtime.graphs import TEAM_ROLE_ROUTE
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
)
from awesome_agent.runtime.team_role_graph import TeamRoleGraph


@pytest.mark.asyncio
async def test_teammate_loads_assignment_permissions() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamRoleGraph(team_repository=teams)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.read", "repo.apply_patch", "shell.execute"],
            deferred_tools=["repo.apply_patch", "shell.execute"],
            promoted_tools=["repo.apply_patch"],
            allowed_skills=["repository-inspection"],
        )
    )

    state, recovered = await graph.execute(run, agent, repository=runtime)

    assert not recovered
    assert state["allowed_tools"] == ["repo.read", "repo.apply_patch"]
    assert state["allowed_skills"] == ["repository-inspection"]


@pytest.mark.asyncio
async def test_teammate_can_create_limited_subagents() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamRoleGraph(team_repository=teams)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.read", "shell.execute"],
            deferred_tools=["shell.execute"],
            can_delegate=True,
            max_subagents=3,
            handoff_context={"subagent_goals": ["Read README", "Inspect tests"]},
        )
    )

    with pytest.raises(ChildRunWait, match="waiting_subagents"):
        await graph.execute(run, agent, repository=runtime)

    subagents = await runtime.list_child_runs(run.id)
    assignments = await teams.list_assignments(run.root_run_id or run.id)
    subagent_assignments = [
        item for item in assignments if item.parent_run_id == run.id
    ]

    assert len(subagents) == 2
    assert all(child.depth == 2 for child in subagents)
    assert [item.kind for item in subagent_assignments] == [
        TeamAssignmentKind.SUBAGENT,
        TeamAssignmentKind.SUBAGENT,
    ]
    assert all(item.allowed_tools == ["repo.read"] for item in subagent_assignments)


@pytest.mark.asyncio
async def test_subagent_cannot_delegate() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamRoleGraph(team_repository=teams)
    run, agent = _role_run(kind=TeamAssignmentKind.SUBAGENT)
    await runtime.create_run(run, agent)
    await teams.create_assignment(_assignment(run, kind=TeamAssignmentKind.SUBAGENT))

    state, _ = await graph.execute(run, agent, repository=runtime)

    assert state["phase"] == "completed"
    assert await runtime.list_child_runs(run.id) == []


@pytest.mark.asyncio
async def test_teammate_resumes_after_subagents_terminal() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamRoleGraph(team_repository=teams)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            can_delegate=True,
            max_subagents=1,
            handoff_context={"subagent_goals": ["Read README"]},
        )
    )

    with pytest.raises(ChildRunWait):
        await graph.execute(run, agent, repository=runtime)
    subagent_assignment = next(
        item
        for item in await teams.list_assignments(run.root_run_id or run.id)
        if item.kind is TeamAssignmentKind.SUBAGENT
    )
    await teams.record_child_terminal(
        subagent_assignment.child_run_id,
        status=TeamAssignmentStatus.COMPLETED,
    )

    state, recovered = await graph.execute(run, agent, repository=runtime)

    assert not recovered
    assert state["phase"] == "completed"


@pytest.mark.asyncio
async def test_teammate_records_patch_artifact_result(tmp_path: Path) -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    graph = TeamRoleGraph(
        team_repository=teams,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=artifacts,
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            can_delegate=False,
            handoff_context={
                "patch": "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n",
                "changed_files": ["README.md"],
            },
        ).model_copy(update={"can_write": True})
    )

    await graph.execute(run, agent, repository=runtime)

    result = (await teams.list_child_results(run.parent_run_id or run.id))[0]
    assert result.patch_artifact_id is not None
    assert result.changed_files == ["README.md"]
    assert (await artifacts.get(result.patch_artifact_id)).artifact_type == "patch"


@pytest.mark.asyncio
async def test_teammate_compacts_large_child_result_summary(tmp_path: Path) -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    budgets = InMemoryBudgetRepository()
    graph = TeamRoleGraph(
        team_repository=teams,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=artifacts,
        budget_repository=budgets,
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            handoff_context={"result_summary": "large evidence " * 2000},
        )
    )

    await graph.execute(run, agent, repository=runtime)

    result = (await teams.list_child_results(run.parent_run_id or run.id))[0]
    assert "offloaded to artifact" in result.summary
    assert result.evidence_artifact_refs
    metadata = await artifacts.get(result.evidence_artifact_refs[0])
    assert metadata.artifact_type == "team-context"
    assert await budgets.list_compactions(run.id)


def _role_run(kind: TeamAssignmentKind) -> tuple[Run, Agent]:
    root_id = Run(goal="root", mode=RunMode.TEAM).id
    parent_id = root_id if kind is TeamAssignmentKind.TEAMMATE else Run(goal="p").id
    run = Run(
        goal=kind.value,
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        parent_run_id=parent_id,
        root_run_id=root_id,
        depth=(1 if kind is TeamAssignmentKind.TEAMMATE else 2),
        child_role=kind.value,
        runtime_route=TEAM_ROLE_ROUTE,
    )
    agent = Agent(
        run_id=run.id,
        kind=(
            AgentKind.TEAMMATE
            if kind is TeamAssignmentKind.TEAMMATE
            else AgentKind.SUBAGENT
        ),
        profile=kind.value,
        model="fake",
    )
    return run, agent


def _assignment(
    run: Run,
    *,
    kind: TeamAssignmentKind,
    allowed_tools: list[str] | None = None,
    deferred_tools: list[str] | None = None,
    promoted_tools: list[str] | None = None,
    allowed_skills: list[str] | None = None,
    can_delegate: bool = False,
    max_subagents: int = 0,
    handoff_context: dict[str, object] | None = None,
) -> TeamAssignment:
    return TeamAssignment(
        root_run_id=run.root_run_id or run.id,
        parent_run_id=run.parent_run_id or run.id,
        child_run_id=run.id,
        kind=kind,
        role_profile=kind.value,
        runtime_route=TEAM_ROLE_ROUTE,
        goal=run.goal,
        allowed_tools=allowed_tools or [],
        deferred_tools=deferred_tools or [],
        promoted_tools=promoted_tools or [],
        allowed_skills=allowed_skills or [],
        can_delegate=can_delegate,
        max_subagents=max_subagents,
        handoff_context=handoff_context or {},
    )
