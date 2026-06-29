from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from awesome_agent.sandbox.process import run_process

TeamPatchAggregationStatus = Literal["applied", "already_applied", "conflict"]
TeamPatchConflictKind = Literal[
    "patch_does_not_apply",
    "already_exists",
    "malformed_patch",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class TeamPatchAggregationResult:
    status: TeamPatchAggregationStatus
    summary: str
    conflict_kind: TeamPatchConflictKind | None = None


async def apply_team_patch(
    workspace: Path,
    patch: str,
) -> TeamPatchAggregationResult:
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
                return TeamPatchAggregationResult(
                    status="already_applied",
                    summary="Patch postimage is already present in the root workspace.",
                )
            summary = _summarize_git_failure(checked.stdout, checked.stderr)
            return TeamPatchAggregationResult(
                status="conflict",
                conflict_kind=_classify_conflict(summary),
                summary=summary,
            )
        process = await run_process(
            ["git", "apply", "--whitespace=nowarn", str(patch_file.name)],
            command_label="git apply team patch",
            workspace=workspace,
            timeout_seconds=30,
        )
        if process.exit_code != 0:
            summary = _summarize_git_failure(process.stdout, process.stderr)
            return TeamPatchAggregationResult(
                status="conflict",
                conflict_kind=_classify_conflict(summary),
                summary=summary,
            )
        return TeamPatchAggregationResult(
            status="applied",
            summary="Patch applied to the root workspace.",
        )
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


def _summarize_git_failure(stdout: str, stderr: str) -> str:
    text = (stderr or stdout or "git apply failed").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:6]) or "git apply failed"


def _classify_conflict(summary: str) -> TeamPatchConflictKind:
    lowered = summary.lower()
    if "patch does not apply" in lowered:
        return "patch_does_not_apply"
    if "already exists in working directory" in lowered or "already exists" in lowered:
        return "already_exists"
    if "corrupt patch" in lowered:
        return "malformed_patch"
    return "unknown"
