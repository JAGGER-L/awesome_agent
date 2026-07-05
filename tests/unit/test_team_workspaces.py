from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from awesome_agent.domain.enums import RunMode, WorkspaceState
from awesome_agent.domain.models import Run
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.dispatch import PermanentExecutionError
from awesome_agent.runtime.team_workspaces import TeamWorkspaceAllocator


class FakeWorktrees:
    def __init__(self, target: Path) -> None:
        self.target = target
        self.calls: list[dict[str, object]] = []

    async def provision(
        self,
        *,
        repository: Path,
        repository_id: UUID,
        run_id: UUID,
        base_commit: str,
    ) -> Path:
        self.calls.append(
            {
                "repository": repository,
                "repository_id": repository_id,
                "run_id": run_id,
                "base_commit": base_commit,
            }
        )
        return self.target

    def branch_for(self, run_id: UUID) -> str:
        return f"awesome-agent/run/{run_id}"


def _parent(tmp_path: Path) -> Run:
    return Run(
        goal="root",
        mode=RunMode.TEAM,
        repository_id=uuid4(),
        base_commit="abc123",
        workspace_path=tmp_path / "root",
        integration_branch="awesome-agent/run/root",
        workspace_state=WorkspaceState.READY,
    )


def _child(parent: Run) -> Run:
    return Run(
        goal="child",
        mode=RunMode.TEAM,
        repository_id=parent.repository_id,
        base_commit=parent.base_commit,
        parent_run_id=parent.id,
        root_run_id=parent.root_run_id or parent.id,
        depth=parent.depth + 1,
    )


@pytest.mark.asyncio
async def test_read_only_child_inherits_parent_workspace(tmp_path: Path) -> None:
    parent = _parent(tmp_path)
    child = _child(parent)
    allocator = TeamWorkspaceAllocator(None)

    assignment = await allocator.assign_for_child(
        parent=parent,
        child=child,
        can_write=False,
    )

    assert assignment.workspace_path == parent.workspace_path
    assert assignment.integration_branch == parent.integration_branch
    assert assignment.workspace_state is WorkspaceState.READY
    assert assignment.isolated is False


@pytest.mark.asyncio
async def test_writing_child_uses_managed_worktree(tmp_path: Path) -> None:
    parent = _parent(tmp_path)
    child = _child(parent)
    fake = FakeWorktrees(tmp_path / "isolated")
    allocator = TeamWorkspaceAllocator(cast(ManagedRunWorktreeManager, fake))

    assignment = await allocator.assign_for_child(
        parent=parent,
        child=child,
        can_write=True,
    )

    assert assignment.workspace_path == tmp_path / "isolated"
    assert assignment.integration_branch == f"awesome-agent/run/{child.id}"
    assert assignment.workspace_state is WorkspaceState.READY
    assert assignment.isolated is True
    assert fake.calls == [
        {
            "repository": parent.workspace_path,
            "repository_id": child.repository_id,
            "run_id": child.id,
            "base_commit": child.base_commit,
        }
    ]


@pytest.mark.asyncio
async def test_writing_child_requires_worktree_manager(tmp_path: Path) -> None:
    parent = _parent(tmp_path)
    child = _child(parent)
    allocator = TeamWorkspaceAllocator(None)

    with pytest.raises(PermanentExecutionError, match="team_worktree_manager"):
        await allocator.assign_for_child(parent=parent, child=child, can_write=True)
