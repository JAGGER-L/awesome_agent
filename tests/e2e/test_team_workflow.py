from pathlib import Path
from uuid import UUID, uuid4

import pytest

from awesome_agent.agents.profiles import AgentProfile, ProfileRegistry
from awesome_agent.domain.enums import AgentKind, TodoStatus
from awesome_agent.domain.models import Agent, TodoItem
from awesome_agent.orchestration.tasks import TaskBoard
from awesome_agent.orchestration.team import TeamRuntime
from awesome_agent.orchestration.verification import (
    VerificationCoordinator,
    VerificationReport,
)

pytestmark = pytest.mark.e2e


class FakeWorkspaces:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def provision(self, agent_id: UUID, profile: AgentProfile) -> Path | None:
        if not profile.can_write:
            return None
        workspace = self.root / str(agent_id)
        workspace.mkdir(parents=True)
        return workspace

    async def release(self, agent_id: UUID) -> None:
        return None


@pytest.mark.asyncio
async def test_team_subagents_rejection_rework_and_completion(
    tmp_path: Path,
) -> None:
    run_id = uuid4()
    leader = Agent(run_id=run_id, kind=AgentKind.LEADER, profile="leader")
    team = TeamRuntime(
        run_id=run_id,
        leader=leader,
        profiles=ProfileRegistry(),
        workspace_provisioner=FakeWorkspaces(tmp_path),
    )
    await team.activate(["frontend-engineer", "backend-engineer"])

    workers = [
        handle
        for handle in team.teammates.values()
        if handle.session.agent.kind is AgentKind.TEAMMATE
    ]
    assert len(workers) == 2
    assert team.verifier().session.agent.kind is AgentKind.VERIFIER

    for worker in workers:
        subagent = worker.create_subagent(profile_name="repo-explorer")
        assert subagent.context_id != worker.session.context_id
        assert not subagent.mailbox_access

    board = TaskBoard(run_id=run_id)
    for worker in workers:
        board.add(
            TodoItem(
                run_id=run_id,
                title=f"Implement {worker.session.agent.profile}",
                status=TodoStatus.IN_PROGRESS,
                primary_owner_id=worker.session.agent.id,
                acceptance_criteria=["tests pass"],
            )
        )

    verifier = team.verifier().session.agent
    coordinator = VerificationCoordinator(team=team, tasks=board)
    tasks = board.list_tasks()

    first = tasks[0]
    assert first.primary_owner_id is not None
    coordinator.submit(first.id, teammate_id=first.primary_owner_id)
    coordinator.begin(first.id, verifier_id=verifier.id)
    coordinator.decide(
        VerificationReport(
            task_id=first.id,
            verifier_id=verifier.id,
            passed=False,
            summary="Initial verification failed.",
        )
    )
    board.transition(first.id, TodoStatus.IN_PROGRESS)
    coordinator.submit(first.id, teammate_id=first.primary_owner_id)
    coordinator.begin(first.id, verifier_id=verifier.id)
    coordinator.decide(
        VerificationReport(
            task_id=first.id,
            verifier_id=verifier.id,
            passed=True,
            summary="Rework passed.",
        )
    )
    coordinator.complete(first.id, leader_id=leader.id)

    second = tasks[1]
    assert second.primary_owner_id is not None
    coordinator.submit(second.id, teammate_id=second.primary_owner_id)
    coordinator.begin(second.id, verifier_id=verifier.id)
    coordinator.decide(
        VerificationReport(
            task_id=second.id,
            verifier_id=verifier.id,
            passed=True,
            summary="Verification passed.",
        )
    )
    coordinator.complete(second.id, leader_id=leader.id)

    assert board.get(first.id).status is TodoStatus.DONE
    assert board.get(second.id).status is TodoStatus.DONE
