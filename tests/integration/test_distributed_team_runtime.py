from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest

from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import (
    AgentKind,
    DispatchStatus,
    EventType,
    RunIntent,
    RunMode,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.artifacts import PostgresArtifactMetadataRepository
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.dispatch import PostgresRunDispatcher
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.persistence.team import PostgresTeamRepository
from awesome_agent.runtime.graphs import TEAM_CODING_GRAPH, TEAM_CODING_VERSION
from awesome_agent.runtime.probe_graph import RuntimeProbeState
from awesome_agent.runtime.team_assignments import TeamAssignmentKind
from awesome_agent.runtime.team_leader_graph import TeamLeaderGraph
from awesome_agent.runtime.team_role_graph import TeamRoleGraph
from awesome_agent.runtime.team_verifier_graph import TeamVerifierGraph
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig
from awesome_agent.sandbox.process import run_process

pytestmark = pytest.mark.integration


class UnusedProbeGraph:
    async def execute(self, _: Run) -> tuple[RuntimeProbeState, bool]:
        raise AssertionError("distributed team test should not execute probe graph")


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_distributed_team_runs_through_workers_with_lineage(
    tmp_path: Path,
) -> None:
    workspace = await _git_workspace(tmp_path)
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    runtime = PostgresRuntimeRepository(sessions)
    teams = PostgresTeamRepository(sessions)
    artifacts = PostgresArtifactMetadataRepository(sessions)
    root = Run(
        goal="Coordinate teammate, subagent, and verifier",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        graph_name=TEAM_CODING_GRAPH,
        graph_version=TEAM_CODING_VERSION,
        dispatch_status=DispatchStatus.QUEUED,
        workspace_path=workspace,
    )
    root = root.model_copy(update={"graph_thread_id": f"run:{root.id}"})
    leader = Agent(
        run_id=root.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    await runtime.create_run(root, leader)
    worker = DurableWorker(
        dispatcher=PostgresRunDispatcher(sessions),
        repository=runtime,
        probe_graph=UnusedProbeGraph(),  # type: ignore[arg-type]
        team_leader_graph=TeamLeaderGraph(
            team_repository=teams,
            artifact_repository=artifacts,
        ),
        team_role_graph=TeamRoleGraph(
            team_repository=teams,
            artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
            artifact_repository=artifacts,
        ),
        team_verifier_graph=TeamVerifierGraph(team_repository=teams),
        config=_worker_config(),
        team_repository=teams,
    )

    await _drain(worker, runtime, root.id)

    restored = await runtime.get_run(root.id)
    descendants = await runtime.list_descendant_runs(root.id)
    assignments = await teams.list_assignments(root.id, include_inactive=True)
    root_results = await teams.list_child_results(root.id)
    teammate = next(run for run in descendants if run.child_role == "teammate")
    subagent_results = await teams.list_child_results(teammate.id)
    mailbox = await teams.list_mailbox_messages(root.id)
    root_events = await runtime.list_events(root.id)
    teammate_events = await runtime.list_events(teammate.id)

    assert restored.status is RunStatus.COMPLETED
    assert [run.child_role for run in descendants] == [
        "teammate",
        "verifier",
        "subagent",
    ]
    assert {item.kind for item in assignments} == {
        TeamAssignmentKind.TEAMMATE,
        TeamAssignmentKind.SUBAGENT,
        TeamAssignmentKind.VERIFIER,
    }
    assert all(item.status == "completed" for item in assignments)
    assert {result.status for result in root_results} == {"completed"}
    assert subagent_results[0].status == "completed"
    assert mailbox[0].route == "verifier_to_leader"
    assert EventType.TEAM_CHILD_RUN_CREATED in {
        event.event_type for event in root_events
    }
    assert EventType.TEAM_CHILD_RUN_CREATED in {
        event.event_type for event in teammate_events
    }
    await engine.dispose()


async def _drain(
    worker: DurableWorker,
    repository: PostgresRuntimeRepository,
    run_id: object,
) -> None:
    for _ in range(10):
        assert await worker.run_once()
        if (await repository.get_run(run_id)).status is RunStatus.COMPLETED:  # type: ignore[arg-type]
            return
    raise AssertionError("distributed team run did not complete")


def _worker_config() -> WorkerConfig:
    return WorkerConfig(
        lease_duration=timedelta(seconds=60),
        heartbeat_interval=timedelta(seconds=15),
        poll_interval=0.01,
        recovery_interval=15,
        shutdown_grace=1,
        retry_delay=timedelta(seconds=0),
        max_attempts=3,
    )


async def _git_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repository"
    workspace.mkdir()
    await _git(workspace, "init")
    await _git(workspace, "config", "user.email", "test@example.com")
    await _git(workspace, "config", "user.name", "Test")
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(workspace, "add", "README.md")
    await _git(workspace, "commit", "-m", "Initial")
    return workspace


async def _git(path: Path, *arguments: str) -> None:
    result = await run_process(
        ["git", *arguments],
        command_label="git fixture",
        workspace=path,
        timeout_seconds=30,
    )
    assert result.exit_code == 0, result.stderr
