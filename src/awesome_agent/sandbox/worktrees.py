from __future__ import annotations

from pathlib import Path

from awesome_agent.sandbox.process import run_process


class GitWorktreeManager:
    def __init__(self, repository: Path) -> None:
        self._repository = repository.resolve()

    async def create(self, target: Path, *, ref: str = "HEAD") -> None:
        result = await run_process(
            ["git", "worktree", "add", "--detach", str(target.resolve()), ref],
            command_label=f"git worktree add {target}",
            workspace=self._repository,
            timeout_seconds=60,
        )
        if result.exit_code != 0:
            raise RuntimeError(result.stderr or result.stdout)

    async def remove(self, target: Path) -> None:
        result = await run_process(
            ["git", "worktree", "remove", "--force", str(target.resolve())],
            command_label=f"git worktree remove {target}",
            workspace=self._repository,
            timeout_seconds=60,
        )
        if result.exit_code != 0:
            raise RuntimeError(result.stderr or result.stdout)
