from __future__ import annotations

import os
from pathlib import Path

import pytest

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.domain.enums import AgentKind, RunIntent, RunMode, RunStatus
from awesome_agent.domain.models import Repository
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.intake_reservations import (
    PostgresIntakeReservationStore,
)
from awesome_agent.persistence.repository_registry import PostgresRepositoryRegistry
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.persistence.team import PostgresTeamRepository
from awesome_agent.repositories.git import require_primary_clean_repository
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.intake import RunIntakeService
from awesome_agent.runtime.worker_app import run_worker
from awesome_agent.sandbox.process import run_process
from awesome_agent.settings import Settings

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ
    or "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL" not in os.environ,
    reason="Runtime and checkpoint databases are not configured.",
)
async def test_team_run_completes_as_distributed_child_runs(tmp_path: Path) -> None:
    repository_path = await _git_workspace(tmp_path)
    snapshot = await require_primary_clean_repository(repository_path)
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    registry = PostgresRepositoryRegistry(sessions)
    registered = await registry.upsert(
        Repository(
            root=snapshot.root,
            display_name="distributed-team-fixture",
            git_common_dir=snapshot.git_common_dir,
            default_branch=snapshot.branch,
        )
    )
    runtime = PostgresRuntimeRepository(sessions)
    teams = PostgresTeamRepository(sessions)
    intake = RunIntakeService(
        registry=registry,
        reservations=PostgresIntakeReservationStore(sessions),
        runtime=runtime,
        events=EventStream(),
        worktrees=ManagedRunWorktreeManager(tmp_path / "worktrees"),
        allowed_roots=[tmp_path],
        model_resolver=_models(),
    )
    run = await intake.create_run(
        repository_id=registered.id,
        goal="Use a teammate, subagent, and verifier to inspect the repository",
        intent=RunIntent.MODIFYING,
        mode=RunMode.TEAM,
    )
    settings = Settings(
        database_url=os.environ["AWESOME_AGENT_TEST_DATABASE_URL"],
        checkpoint_database_url=os.environ[
            "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL"
        ],
        artifact_root=tmp_path / "artifacts",
        worker_poll_interval_seconds=0.01,
    )

    for _ in range(10):
        assert await run_worker(once=True, settings=settings)
        if (await runtime.get_run(run.id)).status is RunStatus.COMPLETED:
            break
    else:
        raise AssertionError("distributed team run did not complete")

    restored = await runtime.get_run(run.id)
    agents = await runtime.list_agents(run.id)
    descendants = await runtime.list_descendant_runs(run.id)
    assignments = await teams.list_assignments(run.id, include_inactive=True)
    messages = await teams.list_mailbox_messages(run.id)

    assert restored.status is RunStatus.COMPLETED
    assert restored.runtime_route == "team-coding"
    assert not hasattr(restored, "graph_version")
    assert [agent.kind for agent in agents] == [AgentKind.LEADER]
    assert [run.child_role for run in descendants] == [
        "teammate",
        "verifier",
        "subagent",
    ]
    assert {assignment.kind.value for assignment in assignments} == {
        "teammate",
        "subagent",
        "verifier",
    }
    assert all(assignment.status.value == "completed" for assignment in assignments)
    assert messages[0].route.value == "verifier_to_leader"
    await engine.dispose()


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="fake-model",
        teammate_model="fake-model",
        verifier_model="fake-model",
        subagent_model="fake-model",
    )


async def _git_workspace(tmp_path: Path) -> Path:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    await _git(repository_path, "init")
    await _git(repository_path, "config", "user.email", "test@example.com")
    await _git(repository_path, "config", "user.name", "Test")
    (repository_path / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(repository_path, "add", "README.md")
    await _git(repository_path, "commit", "-m", "Initial")
    return repository_path


async def _git(path: Path, *arguments: str) -> None:
    result = await run_process(
        ["git", *arguments],
        command_label="git fixture",
        workspace=path,
        timeout_seconds=30,
    )
    assert result.exit_code == 0, result.stderr
