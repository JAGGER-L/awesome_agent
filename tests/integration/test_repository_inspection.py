from pathlib import Path

import pytest

from awesome_agent.repositories.git import (
    InvalidRepository,
    require_primary_clean_repository,
)
from awesome_agent.sandbox.process import run_process

pytestmark = pytest.mark.integration


async def _git(path: Path, *arguments: str) -> None:
    result = await run_process(
        ["git", *arguments],
        command_label="git fixture",
        workspace=path,
        timeout_seconds=30,
    )
    assert result.exit_code == 0, result.stderr


@pytest.mark.asyncio
async def test_inspector_accepts_clean_primary_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    await _git(repository, "init")
    await _git(repository, "config", "user.email", "test@example.com")
    await _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(repository, "add", "README.md")
    await _git(repository, "commit", "-m", "Initial")

    snapshot = await require_primary_clean_repository(repository)

    assert snapshot.root == repository.resolve()
    assert len(snapshot.head_commit) == 40
    assert snapshot.is_clean
    assert not snapshot.is_linked_worktree


@pytest.mark.asyncio
async def test_inspector_rejects_untracked_file(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    await _git(repository, "init")
    await _git(repository, "config", "user.email", "test@example.com")
    await _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(repository, "add", "README.md")
    await _git(repository, "commit", "-m", "Initial")
    (repository / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(InvalidRepository, match="clean"):
        await require_primary_clean_repository(repository)


@pytest.mark.asyncio
async def test_inspector_rejects_linked_worktree(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    worktree = tmp_path / "linked"
    repository.mkdir()
    await _git(repository, "init")
    await _git(repository, "config", "user.email", "test@example.com")
    await _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(repository, "add", "README.md")
    await _git(repository, "commit", "-m", "Initial")
    await _git(repository, "worktree", "add", str(worktree))

    with pytest.raises(InvalidRepository, match="Linked worktrees"):
        await require_primary_clean_repository(worktree)


@pytest.mark.asyncio
async def test_inspector_rejects_in_progress_merge(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    await _git(repository, "init")
    await _git(repository, "config", "user.email", "test@example.com")
    await _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(repository, "add", "README.md")
    await _git(repository, "commit", "-m", "Initial")
    git_dir_result = await run_process(
        ["git", "rev-parse", "--git-dir"],
        command_label="git fixture",
        workspace=repository,
        timeout_seconds=30,
    )
    assert git_dir_result.exit_code == 0
    git_dir = repository / git_dir_result.stdout.strip()
    (git_dir / "MERGE_HEAD").write_text("0" * 40, encoding="ascii")

    with pytest.raises(InvalidRepository, match="merge"):
        await require_primary_clean_repository(repository)
