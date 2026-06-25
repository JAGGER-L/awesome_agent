from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.domain.enums import (
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunStatus,
)
from awesome_agent.domain.models import Repository, Run
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.intake_reservations import (
    PostgresIntakeReservationStore,
)
from awesome_agent.persistence.repository_registry import PostgresRepositoryRegistry
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.repositories.git import require_primary_clean_repository
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.intake import RunIntakeService
from awesome_agent.runtime.probe_graph import (
    RUNTIME_PROBE_GRAPH,
    RUNTIME_PROBE_VERSION,
)
from awesome_agent.runtime.worker_app import run_worker
from awesome_agent.sandbox.process import run_process
from awesome_agent.settings import Settings

pytestmark = pytest.mark.integration


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="deepseek-v4-pro",
        teammate_model="deepseek-v4-flash",
        verifier_model="deepseek-v4-flash",
        subagent_model="deepseek-v4-flash",
    )


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ
    or "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL" not in os.environ,
    reason="Runtime and checkpoint databases are not configured.",
)
async def test_worker_processes_one_durable_runtime_probe(
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    await _git(repository_path, "init")
    await _git(repository_path, "config", "user.email", "test@example.com")
    await _git(repository_path, "config", "user.name", "Test")
    (repository_path / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(repository_path, "add", "README.md")
    await _git(repository_path, "commit", "-m", "Initial")
    snapshot = await require_primary_clean_repository(repository_path)

    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    registry = PostgresRepositoryRegistry(sessions)
    repository = await registry.upsert(
        Repository(
            root=snapshot.root,
            display_name="fixture",
            git_common_dir=snapshot.git_common_dir,
            default_branch=snapshot.branch,
        )
    )
    runtime = PostgresRuntimeRepository(sessions)
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
        repository_id=repository.id,
        goal="Verify durable runtime",
        intent=RunIntent.READ_ONLY,
        execution_kind=ExecutionKind.RUNTIME_PROBE,
        graph_name=RUNTIME_PROBE_GRAPH,
        graph_version=RUNTIME_PROBE_VERSION,
    )

    processed = await run_worker(
        once=True,
        settings=Settings(
            database_url=os.environ["AWESOME_AGENT_TEST_DATABASE_URL"],
            checkpoint_database_url=os.environ[
                "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL"
            ],
        ),
    )

    restored = await runtime.get_run(run.id)
    assert processed
    assert restored.status is RunStatus.COMPLETED
    assert restored.dispatch_status is DispatchStatus.TERMINAL
    assert restored.fencing_token == 1
    await engine.dispose()


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ
    or "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL" not in os.environ,
    reason="Runtime and checkpoint databases are not configured.",
)
async def test_replacement_worker_resumes_after_process_crash(
    tmp_path: Path,
) -> None:
    engine, runtime, run = await _create_probe(tmp_path)
    environment = os.environ.copy()
    process = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "tests/helpers/crash_probe_worker.py"],
        cwd=Path.cwd(),
        env=environment,
        check=False,
        timeout=15,
    )
    assert process.returncode == 91

    await asyncio.sleep(2.5)
    processed = await run_worker(
        once=True,
        settings=Settings(
            database_url=os.environ["AWESOME_AGENT_TEST_DATABASE_URL"],
            checkpoint_database_url=os.environ[
                "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL"
            ],
        ),
    )

    restored = await runtime.get_run(run.id)
    events = await runtime.list_events(run.id)
    assert processed
    assert restored.status is RunStatus.COMPLETED
    assert restored.fencing_token == 2
    assert EventType.DISPATCH_LEASE_EXPIRED in {event.event_type for event in events}
    assert EventType.GRAPH_RECOVERED in {event.event_type for event in events}
    await engine.dispose()


async def _create_probe(
    tmp_path: Path,
) -> tuple[AsyncEngine, PostgresRuntimeRepository, Run]:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    await _git(repository_path, "init")
    await _git(repository_path, "config", "user.email", "test@example.com")
    await _git(repository_path, "config", "user.name", "Test")
    (repository_path / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(repository_path, "add", "README.md")
    await _git(repository_path, "commit", "-m", "Initial")
    snapshot = await require_primary_clean_repository(repository_path)
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    registry = PostgresRepositoryRegistry(sessions)
    repository = await registry.upsert(
        Repository(
            root=snapshot.root,
            display_name="fixture",
            git_common_dir=snapshot.git_common_dir,
            default_branch=snapshot.branch,
        )
    )
    runtime = PostgresRuntimeRepository(sessions)
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
        repository_id=repository.id,
        goal="Verify crash recovery",
        intent=RunIntent.READ_ONLY,
        execution_kind=ExecutionKind.RUNTIME_PROBE,
        graph_name=RUNTIME_PROBE_GRAPH,
        graph_version=RUNTIME_PROBE_VERSION,
    )
    return engine, runtime, run


async def _git(path: Path, *arguments: str) -> None:
    result = await run_process(
        ["git", *arguments],
        command_label="git fixture",
        workspace=path,
        timeout_seconds=30,
    )
    assert result.exit_code == 0, result.stderr
