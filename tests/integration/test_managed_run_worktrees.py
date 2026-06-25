from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.repositories.worktrees import (
    ManagedRunWorktreeManager,
    ManagedWorktreeError,
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


@pytest.mark.asyncio
async def test_managed_worktree_is_named_idempotent_and_reversible(
    tmp_path: Path,
) -> None:
    repository, base_commit = await _repository(tmp_path)
    repository_id = uuid4()
    run_id = uuid4()
    manager = ManagedRunWorktreeManager(tmp_path / "worktrees")

    target = await manager.provision(
        repository=repository,
        repository_id=repository_id,
        run_id=run_id,
        base_commit=base_commit,
    )
    repeated = await manager.provision(
        repository=repository,
        repository_id=repository_id,
        run_id=run_id,
        base_commit=base_commit,
    )

    assert repeated == target
    assert await _git(target, "rev-parse", "HEAD") == base_commit
    assert (
        await _git(target, "branch", "--show-current") == f"awesome-agent/run/{run_id}"
    )
    assert await manager.rollback(
        repository=repository,
        repository_id=repository_id,
        run_id=run_id,
        base_commit=base_commit,
    )
    assert not target.exists()


@pytest.mark.asyncio
async def test_managed_worktree_rejects_target_collision(tmp_path: Path) -> None:
    repository, base_commit = await _repository(tmp_path)
    repository_id = uuid4()
    run_id = uuid4()
    manager = ManagedRunWorktreeManager(tmp_path / "worktrees")
    target = manager.target_for(repository_id, run_id)
    target.mkdir(parents=True)

    with pytest.raises(ManagedWorktreeError, match="already exists"):
        await manager.provision(
            repository=repository,
            repository_id=repository_id,
            run_id=run_id,
            base_commit=base_commit,
        )
