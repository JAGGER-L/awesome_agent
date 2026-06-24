from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from awesome_agent.agents.profiles import AgentProfile, ProfileRegistry
from awesome_agent.domain.enums import AgentKind, AgentStatus
from awesome_agent.domain.models import Agent
from awesome_agent.orchestration.mailbox import TeamMailbox


class WorkspaceProvisioner(Protocol):
    async def provision(self, agent_id: UUID, profile: AgentProfile) -> Path | None:
        """Create an isolated workspace when the profile requires writes."""
        ...

    async def release(self, agent_id: UUID) -> None:
        """Release a previously provisioned workspace."""
        ...


@dataclass(slots=True)
class AgentSession:
    agent: Agent
    context_id: UUID = field(default_factory=uuid4)
    workspace: Path | None = None
    mailbox_access: bool = True
    can_delegate: bool = False
    subagents: dict[UUID, AgentSession] = field(default_factory=dict)


class TeammateHandle:
    def __init__(
        self,
        session: AgentSession,
        *,
        run_id: UUID,
        profiles: ProfileRegistry,
        max_subagents: int,
    ) -> None:
        self.session = session
        self._run_id = run_id
        self._profiles = profiles
        self._max_subagents = max_subagents

    def create_subagent(self, *, profile_name: str) -> AgentSession:
        if len(self.session.subagents) >= self._max_subagents:
            raise RuntimeError("Subagent concurrency limit reached.")
        profile = self._profiles.get(profile_name)
        subagent = AgentSession(
            agent=Agent(
                run_id=self._run_id,
                parent_agent_id=self.session.agent.id,
                kind=AgentKind.SUBAGENT,
                profile=profile.name,
                status=AgentStatus.READY,
            ),
            workspace=self.session.workspace,
            mailbox_access=False,
            can_delegate=False,
        )
        self.session.subagents[subagent.agent.id] = subagent
        return subagent

    def delete_subagent(self, agent_id: UUID) -> None:
        subagent = self.session.subagents.pop(agent_id)
        subagent.agent.status = AgentStatus.DELETED


class TeamRuntime:
    def __init__(
        self,
        *,
        run_id: UUID,
        leader: Agent,
        profiles: ProfileRegistry,
        workspace_provisioner: WorkspaceProvisioner,
        max_teammates: int = 6,
        max_subagents_per_teammate: int = 3,
    ) -> None:
        if leader.kind is not AgentKind.LEADER:
            raise ValueError("Team runtime requires a Leader.")
        self.id = uuid4()
        self.run_id = run_id
        self.leader = leader
        self.profiles = profiles
        self.workspace_provisioner = workspace_provisioner
        self.max_teammates = max_teammates
        self.max_subagents_per_teammate = max_subagents_per_teammate
        self.mailbox = TeamMailbox(team_id=self.id, leader_id=leader.id)
        self.teammates: dict[UUID, TeammateHandle] = {}

    async def activate(self, worker_profiles: list[str]) -> None:
        if self.teammates:
            raise RuntimeError("Team is already active.")
        if len(worker_profiles) + 1 > self.max_teammates:
            raise ValueError("Team exceeds the configured Teammate limit.")

        for profile_name in [*worker_profiles, "verifier"]:
            await self._create_teammate(profile_name)

    async def _create_teammate(self, profile_name: str) -> TeammateHandle:
        profile = self.profiles.get(profile_name)
        kind = AgentKind.VERIFIER if profile.is_verifier else AgentKind.TEAMMATE
        agent = Agent(
            run_id=self.run_id,
            parent_agent_id=self.leader.id,
            kind=kind,
            profile=profile.name,
            status=AgentStatus.READY,
        )
        workspace = await self.workspace_provisioner.provision(agent.id, profile)
        session = AgentSession(
            agent=agent,
            workspace=workspace,
            can_delegate=profile.can_delegate,
        )
        handle = TeammateHandle(
            session,
            run_id=self.run_id,
            profiles=self.profiles,
            max_subagents=self.max_subagents_per_teammate,
        )
        self.teammates[agent.id] = handle
        self.mailbox.add_member(agent.id)
        return handle

    def verifier(self) -> TeammateHandle:
        matches = [
            handle
            for handle in self.teammates.values()
            if handle.session.agent.kind is AgentKind.VERIFIER
        ]
        if len(matches) != 1:
            raise RuntimeError("Active team must have exactly one Verifier.")
        return matches[0]

    async def delete_teammate(self, agent_id: UUID) -> None:
        handle = self.teammates.pop(agent_id)
        for subagent in handle.session.subagents.values():
            subagent.agent.status = AgentStatus.DELETED
        await self.workspace_provisioner.release(agent_id)
        self.mailbox.remove_member(agent_id)
        handle.session.agent.status = AgentStatus.DELETED
