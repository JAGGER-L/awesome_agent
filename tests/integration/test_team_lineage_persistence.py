from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest

from awesome_agent.domain.enums import AgentKind, RunMode, RunStatus
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.persistence.team import PostgresTeamRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
)
from awesome_agent.runtime.team_mailbox import (
    MailboxMessage,
    MailboxMessageStatus,
    MailboxMessageType,
    MailboxRoute,
)

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_team_assignment_round_trips_through_postgres() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    runtime_repository = PostgresRuntimeRepository(sessions)
    team_repository = PostgresTeamRepository(sessions)
    root = await _create_run(runtime_repository, depth=0, child_role=None)
    child = await _create_run(
        runtime_repository,
        root_run_id=root.id,
        parent_run_id=root.id,
        depth=1,
        child_role="teammate",
    )
    assignment = TeamAssignment(
        root_run_id=root.id,
        parent_run_id=root.id,
        child_run_id=child.id,
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="backend-engineer",
        graph_name="team-role",
        graph_version=1,
        goal="Implement backend",
        allowed_tools=["repo.read"],
        allowed_skills=["repository-inspection"],
        can_delegate=True,
        max_subagents=3,
        acceptance_criteria=["Return evidence."],
    )

    saved = await team_repository.create_assignment(assignment)
    loaded = await team_repository.get_assignment(saved.id)
    assignments = await team_repository.list_assignments(root.id)

    assert loaded == saved
    assert assignments == [saved]
    await engine.dispose()


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_mailbox_message_round_trips_through_postgres() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    repository = PostgresTeamRepository(sessions)
    root_run_id = uuid4()
    teammate_run_id = uuid4()
    message = MailboxMessage(
        team_root_run_id=root_run_id,
        sender_run_id=root_run_id,
        sender_agent_id=uuid4(),
        recipient_run_id=teammate_run_id,
        recipient_agent_id=uuid4(),
        route=MailboxRoute.LEADER_TO_TEAMMATE,
        message_type=MailboxMessageType.ASSIGNMENT,
        subject="Inspect repository",
        body_summary="Read README and report findings.",
        requires_response=True,
    )

    saved = await repository.create_mailbox_message(message)
    loaded = await repository.get_mailbox_message(saved.id)
    messages = await repository.list_mailbox_messages(teammate_run_id)

    assert loaded == saved
    assert saved.status is MailboxMessageStatus.UNREAD
    assert messages == [saved]
    await engine.dispose()


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_runtime_lineage_queries_and_waiting_requeue() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    repository = PostgresRuntimeRepository(sessions)
    root = await _create_run(repository, depth=0, child_role=None)
    child = await _create_run(
        repository,
        root_run_id=root.id,
        parent_run_id=root.id,
        depth=1,
        child_role="teammate",
    )
    grandchild = await _create_run(
        repository,
        root_run_id=root.id,
        parent_run_id=child.id,
        depth=2,
        child_role="subagent",
    )
    waiting = root.model_copy(update={"status": RunStatus.WAITING})
    await repository.update_run(waiting)

    children = await repository.list_child_runs(root.id)
    descendants = await repository.list_descendant_runs(root.id)
    requeued = await repository.requeue_waiting_run(root.id, reason="children_done")

    assert children == [child]
    assert descendants == [child, grandchild]
    assert requeued.status is RunStatus.RUNNING
    await engine.dispose()


async def _create_run(
    repository: PostgresRuntimeRepository,
    *,
    root_run_id: UUID | None = None,
    parent_run_id: UUID | None = None,
    depth: int,
    child_role: str | None,
) -> Run:
    run = Run(
        goal=f"team depth {depth}",
        mode=RunMode.TEAM,
        root_run_id=root_run_id,
        parent_run_id=parent_run_id,
        depth=depth,
        child_role=child_role,
    )
    agent = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    await repository.create_run(run, agent)
    return run
