from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.domain.enums import (
    DispatchStatus,
    IntakeReservationStatus,
    RunIntent,
    RunStatus,
)
from awesome_agent.domain.models import IntakeReservation, Repository
from awesome_agent.repositories.git import require_primary_clean_repository
from awesome_agent.repositories.registry import InMemoryRepositoryRegistry
from awesome_agent.repositories.reservations import (
    InMemoryIntakeReservationStore,
)
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.intake import RunIntakeService
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.sandbox.process import run_process

pytestmark = pytest.mark.integration


class FailingPublisher(InMemoryRuntimeRepository):
    async def publish_intake(self, **_: object) -> None:
        raise RuntimeError("deterministic publish failure")


async def _git(path: Path, *arguments: str) -> str:
    result = await run_process(
        ["git", *arguments],
        command_label="git fixture",
        workspace=path,
        timeout_seconds=30,
    )
    assert result.exit_code == 0, result.stderr
    return result.stdout.strip()


async def _registered_repository(
    tmp_path: Path,
) -> tuple[Repository, InMemoryRepositoryRegistry]:
    root = tmp_path / "projects"
    repository_path = root / "repository"
    repository_path.mkdir(parents=True)
    await _git(repository_path, "init")
    await _git(repository_path, "config", "user.email", "test@example.com")
    await _git(repository_path, "config", "user.name", "Test")
    (repository_path / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(repository_path, "add", "README.md")
    await _git(repository_path, "commit", "-m", "Initial")
    snapshot = await require_primary_clean_repository(repository_path)
    registry = InMemoryRepositoryRegistry()
    repository = await registry.upsert(
        Repository(
            root=snapshot.root,
            display_name="repository",
            git_common_dir=snapshot.git_common_dir,
            default_branch=snapshot.branch,
        )
    )
    return repository, registry


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="deepseek-v4-pro",
        teammate_model="deepseek-v4-flash",
        verifier_model="deepseek-v4-flash",
        subagent_model="deepseek-v4-flash",
    )


@pytest.mark.asyncio
async def test_intake_publishes_queued_run_after_worktree_is_ready(
    tmp_path: Path,
) -> None:
    repository, registry = await _registered_repository(tmp_path)
    reservations = InMemoryIntakeReservationStore()
    runtime = InMemoryRuntimeRepository(reservations)
    event_stream = EventStream()
    service = RunIntakeService(
        registry=registry,
        reservations=reservations,
        runtime=runtime,
        events=event_stream,
        worktrees=ManagedRunWorktreeManager(tmp_path / "worktrees"),
        allowed_roots=[tmp_path / "projects"],
        model_resolver=_models(),
    )

    run = await service.create_run(
        repository_id=repository.id,
        goal="Implement intake",
        intent=RunIntent.READ_ONLY,
    )

    assert run.status is RunStatus.CREATED
    assert run.dispatch_status is DispatchStatus.QUEUED
    assert run.workspace_path is not None and run.workspace_path.is_dir()
    assert await _git(run.workspace_path, "rev-parse", "HEAD") == run.base_commit
    assert len(await runtime.list_agents(run.id)) == 1
    assert [event.sequence for event in await runtime.list_events(run.id)] == [1, 2]
    assert event_stream.history(run.id) == await runtime.list_events(run.id)
    assert await reservations.list_incomplete() == []


@pytest.mark.asyncio
async def test_intake_rejects_dirty_repository_before_reservation(
    tmp_path: Path,
) -> None:
    repository, registry = await _registered_repository(tmp_path)
    (repository.root / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    reservations = InMemoryIntakeReservationStore()
    service = RunIntakeService(
        registry=registry,
        reservations=reservations,
        runtime=InMemoryRuntimeRepository(reservations),
        events=EventStream(),
        worktrees=ManagedRunWorktreeManager(tmp_path / "worktrees"),
        allowed_roots=[tmp_path / "projects"],
        model_resolver=_models(),
    )

    with pytest.raises(RuntimeError, match="clean"):
        await service.create_run(
            repository_id=repository.id,
            goal="Should fail",
            intent=RunIntent.MODIFYING,
        )

    assert await reservations.list_incomplete() == []


@pytest.mark.asyncio
async def test_reconcile_closes_reservation_without_git_side_effects(
    tmp_path: Path,
) -> None:
    repository, registry = await _registered_repository(tmp_path)
    reservations = InMemoryIntakeReservationStore()
    runtime = InMemoryRuntimeRepository(reservations)
    worktrees = ManagedRunWorktreeManager(tmp_path / "worktrees")
    service = RunIntakeService(
        registry=registry,
        reservations=reservations,
        runtime=runtime,
        events=EventStream(),
        worktrees=worktrees,
        allowed_roots=[tmp_path / "projects"],
        model_resolver=_models(),
    )
    run_id = uuid4()
    reservation = IntakeReservation(
        run_id=run_id,
        repository_id=repository.id,
        base_commit=await _git(repository.root, "rev-parse", "HEAD"),
        intent=RunIntent.MODIFYING,
        workspace_path=worktrees.target_for(repository.id, run_id),
        integration_branch=worktrees.branch_for(run_id),
    )
    await reservations.create(reservation)

    await service.reconcile_incomplete()

    restored = await reservations.get(reservation.id)
    assert restored.status is IntakeReservationStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_publish_failure_rolls_back_without_public_run(
    tmp_path: Path,
) -> None:
    repository, registry = await _registered_repository(tmp_path)
    reservations = InMemoryIntakeReservationStore()
    runtime = FailingPublisher(reservations)
    service = RunIntakeService(
        registry=registry,
        reservations=reservations,
        runtime=runtime,
        events=EventStream(),
        worktrees=ManagedRunWorktreeManager(tmp_path / "worktrees"),
        allowed_roots=[tmp_path / "projects"],
        model_resolver=_models(),
    )

    with pytest.raises(RuntimeError, match="deterministic"):
        await service.create_run(
            repository_id=repository.id,
            goal="Fail publication",
            intent=RunIntent.MODIFYING,
        )

    assert await reservations.list_incomplete() == []
