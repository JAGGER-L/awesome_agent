from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from awesome_agent.repositories.policy import normalize_path
from awesome_agent.sandbox.process import run_process


class InvalidRepository(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GitRepositorySnapshot:
    root: Path
    git_dir: Path
    git_common_dir: Path
    head_commit: str
    branch: str | None
    is_bare: bool
    is_linked_worktree: bool
    is_clean: bool
    operation: str | None


async def inspect_repository(path: Path) -> GitRepositorySnapshot:
    root = normalize_path(path)
    values = await _git_lines(
        root,
        "rev-parse",
        "--path-format=absolute",
        "--show-toplevel",
        "--git-dir",
        "--git-common-dir",
        "--is-bare-repository",
    )
    if len(values) != 4:
        raise InvalidRepository(f"Unable to inspect Git repository: {root}")

    top_level = normalize_path(Path(values[0]))
    git_dir = normalize_path(Path(values[1]))
    common_dir = normalize_path(Path(values[2]))
    is_bare = values[3].lower() == "true"
    head = (await _git_lines(top_level, "rev-parse", "HEAD"))[0]
    branch_result = await run_process(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        command_label="git symbolic-ref HEAD",
        workspace=top_level,
        timeout_seconds=30,
    )
    branch = branch_result.stdout.strip() if branch_result.exit_code == 0 else None
    status = await run_process(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        command_label="git status",
        workspace=top_level,
        timeout_seconds=30,
    )
    if status.exit_code != 0:
        raise InvalidRepository(status.stderr or status.stdout)

    return GitRepositorySnapshot(
        root=top_level,
        git_dir=git_dir,
        git_common_dir=common_dir,
        head_commit=head,
        branch=branch,
        is_bare=is_bare,
        is_linked_worktree=git_dir != common_dir,
        is_clean=not status.stdout.strip(),
        operation=_operation(common_dir, git_dir),
    )


async def require_primary_clean_repository(path: Path) -> GitRepositorySnapshot:
    snapshot = await inspect_repository(path)
    if snapshot.is_bare:
        raise InvalidRepository("Bare repositories cannot create Runs.")
    if snapshot.is_linked_worktree:
        raise InvalidRepository("Linked worktrees cannot be registered in V1.")
    if not snapshot.is_clean:
        raise InvalidRepository("Repository must be clean, including untracked files.")
    if snapshot.operation is not None:
        raise InvalidRepository(
            f"Repository has an in-progress Git operation: {snapshot.operation}"
        )
    return snapshot


async def _git_lines(workspace: Path, *arguments: str) -> list[str]:
    result = await run_process(
        ["git", *arguments],
        command_label=f"git {' '.join(arguments)}",
        workspace=workspace,
        timeout_seconds=30,
    )
    if result.exit_code != 0:
        raise InvalidRepository(result.stderr or result.stdout)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _operation(common_dir: Path, git_dir: Path) -> str | None:
    checks = [
        (common_dir / "MERGE_HEAD", "merge"),
        (common_dir / "CHERRY_PICK_HEAD", "cherry-pick"),
        (common_dir / "REVERT_HEAD", "revert"),
        (common_dir / "BISECT_LOG", "bisect"),
        (git_dir / "rebase-merge", "rebase"),
        (git_dir / "rebase-apply", "rebase"),
    ]
    return next((name for marker, name in checks if marker.exists()), None)
