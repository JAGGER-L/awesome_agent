from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.artifacts.repository import InMemoryArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import (
    AgentKind,
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunMode,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.budget import (
    InMemoryBudgetRepository,
    RunBudgetLedgerRecord,
)
from awesome_agent.persistence.team import InMemoryTeamRepository
from awesome_agent.runtime.budget import BudgetPolicy
from awesome_agent.runtime.dispatch import ChildRunWait, PermanentExecutionError
from awesome_agent.runtime.graphs import (
    TEAM_CODING_GRAPH,
    TEAM_VERIFIER_GRAPH,
)
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
    TeamChildResult,
)
from awesome_agent.runtime.team_leader_graph import TeamLeaderGraph


@pytest.mark.asyncio
async def test_leader_creates_teammate_child_run_and_assignment() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamLeaderGraph(team_repository=teams)
    run, leader = _leader_run()
    await runtime.create_run(run, leader)
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    with pytest.raises(ChildRunWait, match="waiting_children"):
        await graph.execute(run, leader, repository=runtime, event_sink=emit)

    children = await runtime.list_child_runs(run.id)
    assignments = await teams.list_assignments(run.id)

    assert len(children) == 1
    assert children[0].parent_run_id == run.id
    assert children[0].root_run_id == run.id
    assert children[0].depth == 1
    assert children[0].dispatch_status is DispatchStatus.QUEUED
    assert len(assignments) == 1
    assert assignments[0].kind is TeamAssignmentKind.TEAMMATE
    assert assignments[0].child_run_id == children[0].id
    assert assignments[0].allowed_tools == []
    assert len(events) == 2
    assert events[0][1]["root_run_id"] == str(run.id)
    assert events[0][1]["parent_run_id"] == str(run.id)
    assert events[0][1]["child_run_id"] == str(children[0].id)
    assert events[0][1]["assignment_id"] == str(assignments[0].id)
    assert events[0][1]["agent_id"]


@pytest.mark.asyncio
async def test_leader_stops_before_child_creation_when_team_budget_exhausted() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    budgets = InMemoryBudgetRepository()
    graph = TeamLeaderGraph(
        team_repository=teams,
        budget_repository=budgets,
        budget_policy=_tight_budget_policy(),
    )
    run, leader = _leader_run()
    await runtime.create_run(run, leader)
    await budgets.upsert_ledger(
        RunBudgetLedgerRecord(
            run_id=run.id,
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

    with pytest.raises(PermanentExecutionError, match="team_budget_exhausted"):
        await graph.execute(run, leader, repository=runtime, event_sink=emit)

    assert await runtime.list_child_runs(run.id) == []
    assert events[0][1]["root_run_id"] == str(run.id)
    assert events[0][1]["scope"] == "team_root"


@pytest.mark.asyncio
async def test_leader_wait_is_idempotent_while_child_is_active() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamLeaderGraph(team_repository=teams)
    run, leader = _leader_run()
    await runtime.create_run(run, leader)

    with pytest.raises(ChildRunWait):
        await graph.execute(run, leader, repository=runtime)
    with pytest.raises(ChildRunWait):
        await graph.execute(run, leader, repository=runtime)

    assert len(await runtime.list_child_runs(run.id)) == 1
    assert len(await teams.list_assignments(run.id)) == 1


@pytest.mark.asyncio
async def test_leader_creates_verifier_after_teammate_terminal() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamLeaderGraph(team_repository=teams)
    run, leader = _leader_run()
    await runtime.create_run(run, leader)

    with pytest.raises(ChildRunWait):
        await graph.execute(run, leader, repository=runtime)
    assignment = (await teams.list_assignments(run.id))[0]
    await teams.record_child_terminal(
        assignment.child_run_id,
        status=TeamAssignmentStatus.COMPLETED,
    )

    with pytest.raises(ChildRunWait, match="waiting_verifier"):
        await graph.execute(run, leader, repository=runtime)

    assignments = await teams.list_assignments(run.id)
    verifier = next(
        item for item in assignments if item.kind is TeamAssignmentKind.VERIFIER
    )
    verifier_run = await runtime.get_run(verifier.child_run_id)
    assert verifier.graph_name == TEAM_VERIFIER_GRAPH
    assert not hasattr(verifier, "graph_version")
    assert verifier_run.child_role == "verifier"


@pytest.mark.asyncio
async def test_leader_aggregates_child_patch_artifact(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    store = LocalArtifactStore(tmp_path / ".artifacts")
    graph = TeamLeaderGraph(team_repository=teams, artifact_repository=artifacts)
    run, leader = _leader_run()
    run = run.model_copy(update={"workspace_path": tmp_path})
    await runtime.create_run(run, leader)
    child = Run(
        goal="child",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        parent_run_id=run.id,
        root_run_id=run.id,
        depth=1,
        child_role="teammate",
        graph_name="team-role",
    )
    await runtime.create_run(
        child,
        Agent(
            run_id=child.id,
            parent_agent_id=leader.id,
            kind=AgentKind.TEAMMATE,
            profile="teammate",
            model="fake",
        ),
    )
    assignment = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=child.id,
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="teammate",
        graph_name="team-role",
        goal="child",
    )
    await teams.create_assignment(assignment)
    metadata = store.write(
        run_id=child.id,
        agent_id=None,
        artifact_type="patch",
        filename="change.patch",
        content=(b"--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n"),
        mime_type="text/x-diff",
        summary="README patch",
    )
    await artifacts.record(metadata)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=assignment.id,
            child_run_id=child.id,
            parent_run_id=run.id,
            root_run_id=run.id,
            status="completed",
            summary="Changed README.",
            patch_artifact_id=metadata.id,
            changed_files=["README.md"],
        )
    )
    await teams.record_child_terminal(
        child.id,
        status=TeamAssignmentStatus.COMPLETED,
    )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    with pytest.raises(ChildRunWait, match="waiting_verifier"):
        await graph.execute(run, leader, repository=runtime, event_sink=emit)

    result = (await teams.list_child_results(run.id))[0]
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "new\n"
    assert result.patch_aggregated
    assert any(event[0] is EventType.TEAM_PATCH_AGGREGATED for event in events)


@pytest.mark.asyncio
async def test_leader_completes_after_verifier_assignment_terminal() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamLeaderGraph(team_repository=teams)
    run, leader = _leader_run()
    await runtime.create_run(run, leader)

    with pytest.raises(ChildRunWait):
        await graph.execute(run, leader, repository=runtime)
    teammate = (await teams.list_assignments(run.id))[0]
    await teams.record_child_terminal(
        teammate.child_run_id,
        status=TeamAssignmentStatus.COMPLETED,
    )
    with pytest.raises(ChildRunWait):
        await graph.execute(run, leader, repository=runtime)
    verifier = next(
        item
        for item in await teams.list_assignments(run.id)
        if item.kind is TeamAssignmentKind.VERIFIER
    )
    await teams.record_child_terminal(
        verifier.child_run_id,
        status=TeamAssignmentStatus.COMPLETED,
    )

    state, recovered = await graph.execute(run, leader, repository=runtime)

    assert not recovered
    assert state["phase"] == "completed"
    assert state["final_answer"] == "Distributed team child Runs completed."


@pytest.mark.asyncio
async def test_worker_releases_parent_for_child_wait() -> None:
    from tests.unit.test_worker import (
        FakeDispatcher,
        FakeGraph,
        _config,
        _lease,
    )

    from awesome_agent.runtime.worker import DurableWorker

    lease = _lease()
    run = Run(
        id=lease.run_id,
        goal="team",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        graph_name=TEAM_CODING_GRAPH,
        graph_thread_id=f"run:{lease.run_id}",
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    dispatcher = FakeDispatcher(lease)
    runtime = InMemoryRuntimeRepository()
    await runtime.create_run(run, leader)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=runtime,
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        team_leader_graph=TeamLeaderGraph(team_repository=InMemoryTeamRepository()),
        config=_config(),
    )

    assert await worker.run_once()
    assert dispatcher.calls[-1][0] == "child_wait"
    assert dispatcher.calls[-1][1] == {"reason": "waiting_children"}


@pytest.mark.asyncio
async def test_worker_child_completion_requeues_waiting_parent() -> None:
    from tests.unit.test_worker import FakeDispatcher, FakeGraph, _config, _lease

    from awesome_agent.runtime.worker import DurableWorker

    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    parent, leader = _leader_run()
    waiting_parent = parent.model_copy(update={"status": RunStatus.WAITING})
    child = Run(
        goal="child",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        parent_run_id=parent.id,
        root_run_id=parent.id,
        depth=1,
        child_role="teammate",
        execution_kind=ExecutionKind.RUNTIME_PROBE,
        graph_name="runtime-probe",
    )
    child_agent = Agent(
        run_id=child.id,
        parent_agent_id=leader.id,
        kind=AgentKind.TEAMMATE,
        profile="teammate",
        model="fake",
    )
    await runtime.create_run(waiting_parent, leader)
    await runtime.create_run(child, child_agent)
    from awesome_agent.runtime.team_assignments import TeamAssignment

    await teams.create_assignment(
        TeamAssignment(
            root_run_id=parent.id,
            parent_run_id=parent.id,
            child_run_id=child.id,
            kind=TeamAssignmentKind.TEAMMATE,
            role_profile="teammate",
            graph_name="team-role",
            goal="child",
        )
    )
    lease = _lease().model_copy(update={"run_id": child.id})
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=runtime,
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        config=_config(),
        team_repository=teams,
    )

    assert await worker.run_once()
    requeued = await runtime.get_run(parent.id)
    assignment = await teams.get_assignment_for_child_run(child.id)

    assert assignment.status is TeamAssignmentStatus.COMPLETED
    assert requeued.dispatch_status is DispatchStatus.QUEUED


def _leader_run() -> tuple[Run, Agent]:
    run = Run(
        id=uuid4(),
        goal="Implement backend and verify it",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        graph_name=TEAM_CODING_GRAPH,
        graph_thread_id=f"run:{uuid4()}",
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="deepseek-v4-pro",
    )
    return run, leader


def _tight_budget_policy() -> BudgetPolicy:
    return BudgetPolicy(
        soft_context_tokens=1000,
        hard_context_tokens=2000,
        recent_context_tokens=800,
        max_total_tokens_per_run=120,
        max_reasoning_tokens_per_run=1000,
        max_active_seconds_per_run=3600,
    )


def _git(path: Path, *arguments: str) -> None:
    import subprocess

    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
        capture_output=True,
        check=True,
        text=True,
    )
    assert result.returncode == 0
