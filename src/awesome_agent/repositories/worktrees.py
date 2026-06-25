from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from awesome_agent.repositories.policy import normalize_path
from awesome_agent.sandbox.process import run_process


class ManagedWorktreeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorktreeOwnership:
    run_id: str
    repository_id: str
    integration_branch: str
    base_commit: str
    created_at: str


class ManagedRunWorktreeManager:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = normalize_path(workspace_root)

    def target_for(self, repository_id: UUID, run_id: UUID) -> Path:
        return self.workspace_root / str(repository_id) / str(run_id)

    def branch_for(self, run_id: UUID) -> str:
        return f"awesome-agent/run/{run_id}"

    async def provision(
        self,
        *,
        repository: Path,
        repository_id: UUID,
        run_id: UUID,
        base_commit: str,
    ) -> Path:
        source = normalize_path(repository)
        target = self.target_for(repository_id, run_id)
        branch = self.branch_for(run_id)
        self._require_managed_target(target)

        existing = await self._worktree_entry(source, target)
        if existing is not None:
            head, existing_branch = existing
            if head != base_commit or existing_branch != branch:
                raise ManagedWorktreeError(
                    "Managed worktree path is owned by different Git state."
                )
            self._write_owner(
                repository_id=repository_id,
                run_id=run_id,
                branch=branch,
                base_commit=base_commit,
            )
            return target

        if target.exists():
            raise ManagedWorktreeError(
                f"Managed worktree target already exists: {target}"
            )
        if await self._branch_commit(source, branch) is not None:
            raise ManagedWorktreeError(
                f"Integration branch already exists without its worktree: {branch}"
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        self._write_owner(
            repository_id=repository_id,
            run_id=run_id,
            branch=branch,
            base_commit=base_commit,
        )
        result = await run_process(
            [
                "git",
                "worktree",
                "add",
                "-b",
                branch,
                str(target),
                base_commit,
            ],
            command_label=f"git worktree add {branch}",
            workspace=source,
            timeout_seconds=60,
        )
        if result.exit_code != 0:
            raise ManagedWorktreeError(result.stderr or result.stdout)
        return target

    async def rollback(
        self,
        *,
        repository: Path,
        repository_id: UUID,
        run_id: UUID,
        base_commit: str,
    ) -> bool:
        source = normalize_path(repository)
        target = self.target_for(repository_id, run_id)
        branch = self.branch_for(run_id)
        self._require_managed_target(target)
        if not self._owner_matches(
            repository_id=repository_id,
            run_id=run_id,
            branch=branch,
            base_commit=base_commit,
        ):
            return False

        existing = await self._worktree_entry(source, target)
        if existing is not None:
            head, existing_branch = existing
            if head != base_commit or existing_branch != branch:
                return False
            result = await run_process(
                ["git", "worktree", "remove", str(target)],
                command_label=f"git worktree remove {branch}",
                workspace=source,
                timeout_seconds=60,
            )
            if result.exit_code != 0:
                return False

        branch_commit = await self._branch_commit(source, branch)
        if branch_commit is not None:
            if branch_commit != base_commit:
                return False
            result = await run_process(
                ["git", "branch", "-D", branch],
                command_label=f"git branch delete {branch}",
                workspace=source,
                timeout_seconds=30,
            )
            if result.exit_code != 0:
                return False

        self._owner_path(repository_id, run_id).unlink(missing_ok=True)
        return True

    async def _worktree_entry(
        self, repository: Path, target: Path
    ) -> tuple[str, str] | None:
        result = await run_process(
            ["git", "worktree", "list", "--porcelain"],
            command_label="git worktree list",
            workspace=repository,
            timeout_seconds=30,
        )
        if result.exit_code != 0:
            raise ManagedWorktreeError(result.stderr or result.stdout)
        entries = _parse_worktrees(result.stdout)
        return entries.get(normalize_path(target))

    async def _branch_commit(self, repository: Path, branch: str) -> str | None:
        result = await run_process(
            ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
            command_label=f"git rev-parse {branch}",
            workspace=repository,
            timeout_seconds=30,
        )
        if result.exit_code != 0:
            return None
        return result.stdout.strip()

    def _owner_path(self, repository_id: UUID, run_id: UUID) -> Path:
        return (
            self.workspace_root
            / str(repository_id)
            / ".awesome-agent-owners"
            / f"{run_id}.json"
        )

    def _write_owner(
        self,
        *,
        repository_id: UUID,
        run_id: UUID,
        branch: str,
        base_commit: str,
    ) -> None:
        path = self._owner_path(repository_id, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        ownership = WorktreeOwnership(
            run_id=str(run_id),
            repository_id=str(repository_id),
            integration_branch=branch,
            base_commit=base_commit,
            created_at=datetime.now(UTC).isoformat(),
        )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(asdict(ownership), stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _owner_matches(
        self,
        *,
        repository_id: UUID,
        run_id: UUID,
        branch: str,
        base_commit: str,
    ) -> bool:
        path = self._owner_path(repository_id, run_id)
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict):
            return False
        return (
            data.get("run_id") == str(run_id)
            and data.get("repository_id") == str(repository_id)
            and data.get("integration_branch") == branch
            and data.get("base_commit") == base_commit
        )

    def _require_managed_target(self, target: Path) -> None:
        normalized = normalize_path(target)
        if normalized == self.workspace_root or not normalized.is_relative_to(
            self.workspace_root
        ):
            raise ManagedWorktreeError("Worktree target is outside managed root.")


def _parse_worktrees(output: str) -> dict[Path, tuple[str, str]]:
    entries: dict[Path, tuple[str, str]] = {}
    current_path: Path | None = None
    current_head: str | None = None
    current_branch: str | None = None
    for line in [*output.splitlines(), ""]:
        if line.startswith("worktree "):
            current_path = normalize_path(Path(line.removeprefix("worktree ")))
        elif line.startswith("HEAD "):
            current_head = line.removeprefix("HEAD ")
        elif line.startswith("branch refs/heads/"):
            current_branch = line.removeprefix("branch refs/heads/")
        elif not line and current_path is not None:
            if current_head is not None and current_branch is not None:
                entries[current_path] = (current_head, current_branch)
            current_path = None
            current_head = None
            current_branch = None
    return entries
