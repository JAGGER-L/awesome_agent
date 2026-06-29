from __future__ import annotations

from pathlib import Path

from awesome_agent.runtime.dispatch import PermanentExecutionError
from awesome_agent.sandbox.process import run_process


async def role_git_diff(workspace: Path) -> str:
    result = await run_process(
        ["git", "diff", "--", "."],
        command_label="team role diff",
        workspace=workspace,
        timeout_seconds=30,
    )
    if result.exit_code != 0:
        raise PermanentExecutionError(
            result.stderr or result.stdout or "git diff failed"
        )
    return result.stdout


async def role_changed_files(workspace: Path) -> list[str]:
    result = await run_process(
        ["git", "diff", "--name-only", "--", "."],
        command_label="team role changed files",
        workspace=workspace,
        timeout_seconds=30,
    )
    if result.exit_code != 0:
        raise PermanentExecutionError(
            result.stderr or result.stdout or "git diff --name-only failed"
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]
