from pathlib import Path

import pytest

from awesome_agent.sandbox.worktrees import GitWorktreeManager

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_worktree_create_and_remove(tmp_path: Path) -> None:
    repository = Path.cwd()
    target = tmp_path / "teammate-worktree"
    manager = GitWorktreeManager(repository)

    await manager.create(target)
    try:
        assert (target / "README.md").exists()
    finally:
        await manager.remove(target)

    assert not target.exists()
