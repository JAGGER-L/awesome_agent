from pathlib import Path
from uuid import UUID

from awesome_agent.agents.profiles import AgentProfile
from awesome_agent.sandbox.worktrees import GitWorktreeManager


class GitWorkspaceProvisioner:
    def __init__(self, *, repository: Path, worktree_root: Path) -> None:
        self._manager = GitWorktreeManager(repository)
        self._root = worktree_root
        self._paths: dict[UUID, Path] = {}

    async def provision(self, agent_id: UUID, profile: AgentProfile) -> Path | None:
        if not profile.can_write:
            return None
        target = self._root / str(agent_id)
        self._root.mkdir(parents=True, exist_ok=True)
        await self._manager.create(target)
        self._paths[agent_id] = target
        return target

    async def release(self, agent_id: UUID) -> None:
        target = self._paths.pop(agent_id, None)
        if target is not None:
            await self._manager.remove(target)
