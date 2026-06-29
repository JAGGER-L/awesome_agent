from __future__ import annotations

from pathlib import Path

from awesome_agent.sandbox.process import run_process


async def apply_team_patch(workspace: Path, patch: str) -> None:
    patch_file = workspace / ".awesome-agent-team.patch"
    patch_file.write_text(patch, encoding="utf-8")
    try:
        checked = await run_process(
            ["git", "apply", "--check", "--whitespace=nowarn", str(patch_file.name)],
            command_label="git apply --check team patch",
            workspace=workspace,
            timeout_seconds=30,
        )
        if checked.exit_code != 0:
            reverse = await run_process(
                [
                    "git",
                    "apply",
                    "--check",
                    "--reverse",
                    "--whitespace=nowarn",
                    str(patch_file.name),
                ],
                command_label="git apply --reverse --check team patch",
                workspace=workspace,
                timeout_seconds=30,
            )
            if reverse.exit_code == 0:
                return
            raise RuntimeError(checked.stderr or checked.stdout or "git apply failed")
        process = await run_process(
            ["git", "apply", "--whitespace=nowarn", str(patch_file.name)],
            command_label="git apply team patch",
            workspace=workspace,
            timeout_seconds=30,
        )
        if process.exit_code != 0:
            raise RuntimeError(process.stderr or process.stdout or "git apply failed")
    finally:
        patch_file.unlink(missing_ok=True)


async def team_aggregation_diff(workspace: Path) -> str:
    process = await run_process(
        ["git", "diff", "--", "."],
        command_label="git diff team aggregation",
        workspace=workspace,
        timeout_seconds=30,
    )
    if process.exit_code != 0:
        raise RuntimeError(process.stderr or process.stdout or "git diff failed")
    return process.stdout
