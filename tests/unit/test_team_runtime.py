from pathlib import Path
from uuid import UUID, uuid4

import pytest

from awesome_agent.agents.profiles import AgentProfile, ProfileRegistry
from awesome_agent.domain.enums import AgentKind
from awesome_agent.domain.models import Agent
from awesome_agent.orchestration.team import TeamRuntime


class FakeWorkspaceProvisioner:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.provisioned: dict[UUID, Path] = {}

    async def provision(self, agent_id: UUID, profile: AgentProfile) -> Path | None:
        if not profile.can_write:
            return None
        path = self.root / str(agent_id)
        self.provisioned[agent_id] = path
        return path

    async def release(self, agent_id: UUID) -> None:
        self.provisioned.pop(agent_id, None)


def _leader(run_id: UUID) -> Agent:
    return Agent(run_id=run_id, kind=AgentKind.LEADER, profile="leader")


@pytest.mark.asyncio
async def test_team_activation_always_adds_verifier(tmp_path: Path) -> None:
    run_id = uuid4()
    team = TeamRuntime(
        run_id=run_id,
        leader=_leader(run_id),
        profiles=ProfileRegistry(),
        workspace_provisioner=FakeWorkspaceProvisioner(tmp_path),
    )

    await team.activate(["backend-engineer", "frontend-engineer"])

    assert len(team.teammates) == 3
    assert team.verifier().session.agent.profile == "verifier"


@pytest.mark.asyncio
async def test_teammate_creates_isolated_subagents(tmp_path: Path) -> None:
    run_id = uuid4()
    team = TeamRuntime(
        run_id=run_id,
        leader=_leader(run_id),
        profiles=ProfileRegistry(),
        workspace_provisioner=FakeWorkspaceProvisioner(tmp_path),
    )
    await team.activate(["backend-engineer"])
    teammate = next(
        handle
        for handle in team.teammates.values()
        if handle.session.agent.kind is AgentKind.TEAMMATE
    )

    subagent = teammate.create_subagent(profile_name="repo-explorer")

    assert subagent.agent.parent_agent_id == teammate.session.agent.id
    assert not subagent.mailbox_access
    assert not subagent.can_delegate
    assert subagent.context_id != teammate.session.context_id


@pytest.mark.asyncio
async def test_subagent_limit_is_per_teammate(tmp_path: Path) -> None:
    run_id = uuid4()
    team = TeamRuntime(
        run_id=run_id,
        leader=_leader(run_id),
        profiles=ProfileRegistry(),
        workspace_provisioner=FakeWorkspaceProvisioner(tmp_path),
    )
    await team.activate(["backend-engineer"])
    teammate = next(
        handle
        for handle in team.teammates.values()
        if handle.session.agent.kind is AgentKind.TEAMMATE
    )

    for _ in range(3):
        teammate.create_subagent(profile_name="repo-explorer")

    with pytest.raises(RuntimeError, match="limit"):
        teammate.create_subagent(profile_name="repo-explorer")


@pytest.mark.asyncio
async def test_leader_can_observe_all_mailbox_messages(tmp_path: Path) -> None:
    run_id = uuid4()
    leader = _leader(run_id)
    team = TeamRuntime(
        run_id=run_id,
        leader=leader,
        profiles=ProfileRegistry(),
        workspace_provisioner=FakeWorkspaceProvisioner(tmp_path),
    )
    await team.activate(["backend-engineer", "frontend-engineer"])
    workers = [
        handle.session.agent
        for handle in team.teammates.values()
        if handle.session.agent.kind is AgentKind.TEAMMATE
    ]

    team.mailbox.send(
        sender_id=workers[0].id,
        recipient_id=workers[1].id,
        content="API contract is ready.",
    )

    assert len(team.mailbox.list_for(leader.id)) == 1
