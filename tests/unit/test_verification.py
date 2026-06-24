from pathlib import Path
from uuid import UUID, uuid4

import pytest

from awesome_agent.agents.profiles import AgentProfile, ProfileRegistry
from awesome_agent.domain.enums import AgentKind, TodoStatus
from awesome_agent.domain.models import Agent, TodoItem
from awesome_agent.orchestration.tasks import TaskBoard
from awesome_agent.orchestration.team import TeamRuntime
from awesome_agent.orchestration.verification import (
    VerificationCheck,
    VerificationCoordinator,
    VerificationReport,
)


class NoopWorkspaceProvisioner:
    async def provision(self, agent_id: UUID, profile: AgentProfile) -> Path | None:
        return None

    async def release(self, agent_id: UUID) -> None:
        return None


async def _team_and_task() -> tuple[TeamRuntime, TaskBoard, TodoItem]:
    run_id = uuid4()
    leader = Agent(run_id=run_id, kind=AgentKind.LEADER, profile="leader")
    team = TeamRuntime(
        run_id=run_id,
        leader=leader,
        profiles=ProfileRegistry(),
        workspace_provisioner=NoopWorkspaceProvisioner(),
    )
    await team.activate(["backend-engineer"])
    owner = next(
        handle.session.agent
        for handle in team.teammates.values()
        if handle.session.agent.kind is AgentKind.TEAMMATE
    )
    task = TodoItem(
        run_id=run_id,
        title="Implement API",
        status=TodoStatus.IN_PROGRESS,
        primary_owner_id=owner.id,
        acceptance_criteria=["tests pass"],
    )
    board = TaskBoard(run_id=run_id)
    board.add(task)
    return team, board, task


@pytest.mark.asyncio
async def test_rejected_work_returns_to_same_owner() -> None:
    team, board, task = await _team_and_task()
    coordinator = VerificationCoordinator(team=team, tasks=board)
    verifier = team.verifier().session.agent
    owner_id = task.primary_owner_id
    assert owner_id is not None

    coordinator.submit(task.id, teammate_id=owner_id)
    coordinator.begin(task.id, verifier_id=verifier.id)
    status = coordinator.decide(
        VerificationReport(
            task_id=task.id,
            verifier_id=verifier.id,
            passed=False,
            summary="Tests failed.",
            checks=[
                VerificationCheck(name="pytest", passed=False, evidence="1 failed")
            ],
        )
    )

    assert status is TodoStatus.REJECTED
    assert board.get(task.id).primary_owner_id == owner_id
    board.transition(task.id, TodoStatus.IN_PROGRESS)


@pytest.mark.asyncio
async def test_only_verified_work_can_be_completed() -> None:
    team, board, task = await _team_and_task()
    coordinator = VerificationCoordinator(team=team, tasks=board)
    verifier = team.verifier().session.agent
    owner_id = task.primary_owner_id
    assert owner_id is not None

    with pytest.raises(ValueError, match="Invalid todo transition"):
        coordinator.complete(task.id, leader_id=team.leader.id)

    coordinator.submit(task.id, teammate_id=owner_id)
    coordinator.begin(task.id, verifier_id=verifier.id)
    coordinator.decide(
        VerificationReport(
            task_id=task.id,
            verifier_id=verifier.id,
            passed=True,
            summary="All checks passed.",
        )
    )
    coordinator.complete(task.id, leader_id=team.leader.id)

    assert board.get(task.id).status is TodoStatus.DONE


@pytest.mark.asyncio
async def test_implementer_cannot_verify_own_work() -> None:
    team, board, task = await _team_and_task()
    coordinator = VerificationCoordinator(team=team, tasks=board)
    owner_id = task.primary_owner_id
    assert owner_id is not None
    coordinator.submit(task.id, teammate_id=owner_id)

    with pytest.raises(PermissionError, match="Verifier"):
        coordinator.begin(task.id, verifier_id=owner_id)
