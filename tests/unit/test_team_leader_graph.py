from uuid import uuid4

import pytest

from awesome_agent.domain.enums import (
    AgentKind,
    DispatchStatus,
    ExecutionKind,
    RunIntent,
    RunMode,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.team import InMemoryTeamRepository
from awesome_agent.runtime.dispatch import ChildRunWait
from awesome_agent.runtime.graphs import (
    TEAM_CODING_GRAPH,
    TEAM_CODING_VERSION,
    TEAM_VERIFIER_GRAPH,
    TEAM_VERIFIER_VERSION,
)
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignmentKind,
    TeamAssignmentStatus,
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
    assert verifier.graph_version == TEAM_VERIFIER_VERSION
    assert verifier_run.child_role == "verifier"


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
        graph_version=TEAM_CODING_VERSION,
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
        graph_version=1,
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
            graph_version=1,
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
        graph_version=TEAM_CODING_VERSION,
        graph_thread_id=f"run:{uuid4()}",
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="deepseek-v4-pro",
    )
    return run, leader
