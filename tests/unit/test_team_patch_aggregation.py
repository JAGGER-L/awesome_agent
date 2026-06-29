from pathlib import Path

import pytest

from awesome_agent.runtime.team_patch_aggregation import apply_team_patch
from awesome_agent.sandbox.process import run_process


@pytest.mark.asyncio
async def test_apply_team_patch_reports_applied(tmp_path: Path) -> None:
    await _git_workspace(tmp_path, "old\n")

    result = await apply_team_patch(
        tmp_path,
        (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
    )

    assert result.status == "applied"
    assert result.conflict_kind is None
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_apply_team_patch_reports_already_applied(tmp_path: Path) -> None:
    await _git_workspace(tmp_path, "new\n")

    result = await apply_team_patch(
        tmp_path,
        (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
    )

    assert result.status == "already_applied"
    assert result.conflict_kind is None
    assert "already" in result.summary.lower()
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_apply_team_patch_reports_conflict_without_mutating(
    tmp_path: Path,
) -> None:
    await _git_workspace(tmp_path, "other\n")

    result = await apply_team_patch(
        tmp_path,
        (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
    )

    assert result.status == "conflict"
    assert result.conflict_kind == "patch_does_not_apply"
    assert "README.md" in result.summary
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "other\n"


async def _git_workspace(path: Path, readme: str) -> None:
    await _git(path, "init")
    await _git(path, "config", "user.email", "test@example.com")
    await _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text(readme, encoding="utf-8")
    await _git(path, "add", "README.md")
    await _git(path, "commit", "-m", "Initial")


async def _git(path: Path, *arguments: str) -> None:
    result = await run_process(
        ["git", *arguments],
        command_label="git fixture",
        workspace=path,
        timeout_seconds=30,
    )
    assert result.exit_code == 0, result.stderr
