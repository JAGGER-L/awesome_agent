from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import (
    AgentKind,
    DispatchStatus,
    RunStatus,
    WorkspaceRetentionStatus,
)
from awesome_agent.domain.models import Agent, Repository, Run
from awesome_agent.repositories.registry import InMemoryRepositoryRegistry
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.workspaces import (
    WorkspaceCandidateStatus,
    WorkspaceCleanupRequest,
    WorkspaceRetentionService,
)
from awesome_agent.sandbox.process import run_process

pytestmark = pytest.mark.integration


async def _git(path: Path, *arguments: str) -> str:
    result = await run_process(
        ["git", *arguments],
        command_label="git fixture",
        workspace=path,
        timeout_seconds=30,
    )
    assert result.exit_code == 0, result.stderr
    return result.stdout.strip()


async def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    await _git(repository, "init")
    await _git(repository, "config", "user.email", "test@example.com")
    await _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(repository, "add", "README.md")
    await _git(repository, "commit", "-m", "Initial")
    return repository, await _git(repository, "rev-parse", "HEAD")


async def _service_with_run(
    tmp_path: Path,
    *,
    status: RunStatus = RunStatus.COMPLETED,
) -> tuple[
    WorkspaceRetentionService,
    InMemoryRuntimeRepository,
    ManagedRunWorktreeManager,
    Run,
    Path,
    Path,
]:
    repository_path, base_commit = await _repository(tmp_path)
    registry = InMemoryRepositoryRegistry()
    registered = await registry.upsert(
        Repository(
            root=repository_path,
            display_name="repository",
            git_common_dir=repository_path / ".git",
            default_branch="master",
        )
    )
    runtime = InMemoryRuntimeRepository()
    manager = ManagedRunWorktreeManager(tmp_path / "workspaces")
    run = Run(
        goal="Clean workspace",
        status=status,
        dispatch_status=DispatchStatus.TERMINAL,
        repository_id=registered.id,
        base_commit=base_commit,
        workspace_path=manager.target_for(registered.id, uuid4()),
    )
    run = run.model_copy(update={"integration_branch": manager.branch_for(run.id)})
    workspace = await manager.provision(
        repository=repository_path,
        repository_id=registered.id,
        run_id=run.id,
        base_commit=base_commit,
    )
    run = run.model_copy(update={"workspace_path": workspace})
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="deepseek-v4-pro",
    )
    await runtime.create_run(run, leader)
    return (
        WorkspaceRetentionService(
            runtime_repository=runtime,
            repository_registry=registry,
            worktrees=manager,
        ),
        runtime,
        manager,
        run,
        repository_path,
        workspace,
    )


@pytest.mark.asyncio
async def test_workspace_cleanup_preview_does_not_delete(tmp_path: Path) -> None:
    service, _, manager, run, repository, workspace = await _service_with_run(tmp_path)

    candidates = await service.cleanup_preview(
        WorkspaceCleanupRequest(run_id=run.id),
    )

    assert candidates[0].status is WorkspaceCandidateStatus.ELIGIBLE
    assert workspace.exists()
    branch_commit = await manager.branch_commit(repository, manager.branch_for(run.id))
    assert branch_commit is not None


@pytest.mark.asyncio
async def test_workspace_cleanup_apply_removes_worktree_branch_and_owner(
    tmp_path: Path,
) -> None:
    service, runtime, manager, run, repository, workspace = await _service_with_run(
        tmp_path
    )

    candidates = await service.cleanup(
        WorkspaceCleanupRequest(run_id=run.id, apply=True),
    )

    restored = await runtime.get_run(run.id)
    events = await runtime.list_events(run.id)
    assert candidates[0].status is WorkspaceCandidateStatus.CLEANED
    assert not workspace.exists()
    assert await manager.branch_commit(repository, manager.branch_for(run.id)) is None
    assert run.repository_id is not None
    assert manager.read_owner(run.repository_id, run.id) is None
    assert restored.workspace_retention_status is WorkspaceRetentionStatus.CLEANED
    assert restored.workspace_cleaned_at is not None
    assert events[-1].event_type.value == "workspace.cleaned"


@pytest.mark.asyncio
async def test_dirty_workspace_cleanup_requires_force_with_reason(
    tmp_path: Path,
) -> None:
    service, _, _, run, _, workspace = await _service_with_run(tmp_path)
    (workspace / "README.md").write_text("changed\n", encoding="utf-8")

    blocked = await service.cleanup(
        WorkspaceCleanupRequest(run_id=run.id, apply=True),
    )
    forced = await service.cleanup(
        WorkspaceCleanupRequest(
            run_id=run.id,
            apply=True,
            force=True,
            reason="discard inspected changes",
        ),
    )

    assert blocked[0].status is WorkspaceCandidateStatus.BLOCKED_DIRTY_WORKTREE
    assert forced[0].status is WorkspaceCandidateStatus.CLEANED
    assert not workspace.exists()


@pytest.mark.asyncio
async def test_failed_dirty_workspace_can_be_removed_with_force(
    tmp_path: Path,
) -> None:
    service, _, _, run, _, workspace = await _service_with_run(
        tmp_path,
        status=RunStatus.FAILED,
    )
    (workspace / "README.md").write_text("failed changes\n", encoding="utf-8")

    candidates = await service.cleanup(
        WorkspaceCleanupRequest(
            run_id=run.id,
            apply=True,
            force=True,
            reason="failed run inspected",
        ),
    )

    assert candidates[0].status is WorkspaceCandidateStatus.CLEANED
    assert not workspace.exists()


@pytest.mark.asyncio
async def test_recovery_required_workspace_is_not_removed_with_force(
    tmp_path: Path,
) -> None:
    service, _, _, run, _, workspace = await _service_with_run(
        tmp_path,
        status=RunStatus.RECOVERY_REQUIRED,
    )

    candidates = await service.cleanup(
        WorkspaceCleanupRequest(
            run_id=run.id,
            apply=True,
            force=True,
            reason="try to discard recovery evidence",
        ),
    )

    assert candidates[0].status is WorkspaceCandidateStatus.BLOCKED_RECOVERY_REQUIRED
    assert workspace.exists()
