from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from awesome_agent.domain.models import Run
from awesome_agent.repositories.worktrees import (
    ManagedRunWorktreeManager,
    ManagedWorktreeError,
)
from awesome_agent.runtime.dispatch import PermanentExecutionError


@dataclass(frozen=True, slots=True)
class TeamWorkspaceAssignment:
    workspace_path: Path | None
    integration_branch: str | None
    isolated: bool


class TeamWorkspaceAllocator:
    def __init__(self, worktrees: ManagedRunWorktreeManager | None) -> None:
        self.worktrees = worktrees

    async def assign_for_child(
        self,
        *,
        parent: Run,
        child: Run,
        can_write: bool,
    ) -> TeamWorkspaceAssignment:
        if not can_write:
            return TeamWorkspaceAssignment(
                workspace_path=parent.workspace_path,
                integration_branch=parent.integration_branch,
                isolated=False,
            )
        if self.worktrees is None:
            raise PermanentExecutionError("team_worktree_manager_unavailable")
        if parent.workspace_path is None:
            raise PermanentExecutionError("team_parent_workspace_unavailable")
        if child.repository_id is None:
            raise PermanentExecutionError("team_child_repository_unavailable")
        if child.base_commit is None:
            raise PermanentExecutionError("team_child_base_commit_unavailable")
        try:
            workspace = await self.worktrees.provision(
                repository=parent.workspace_path,
                repository_id=child.repository_id,
                run_id=child.id,
                base_commit=child.base_commit,
            )
        except ManagedWorktreeError as error:
            raise PermanentExecutionError(
                "team_child_worktree_provision_failed"
            ) from error
        return TeamWorkspaceAssignment(
            workspace_path=workspace,
            integration_branch=self.worktrees.branch_for(child.id),
            isolated=True,
        )
