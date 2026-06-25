from pathlib import Path
from uuid import uuid4

from awesome_agent.domain.enums import (
    AgentKind,
    DispatchStatus,
    EventType,
    RunIntent,
    WorkspaceState,
)
from awesome_agent.domain.models import Agent, Run, RuntimeEvent


def test_run_and_agent_have_stable_lineage() -> None:
    run = Run(goal="Implement a feature")
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="deepseek-v4-pro",
    )
    teammate = Agent(
        run_id=run.id,
        parent_agent_id=leader.id,
        kind=AgentKind.TEAMMATE,
        profile="backend-engineer",
        model="deepseek-v4-flash",
    )

    assert teammate.run_id == run.id
    assert teammate.parent_agent_id == leader.id


def test_runtime_event_requires_positive_sequence() -> None:
    event = RuntimeEvent(
        run_id=uuid4(),
        sequence=1,
        event_type=EventType.RUN_CREATED,
    )

    assert event.sequence == 1


def test_run_can_carry_repository_workspace_identity(tmp_path: Path) -> None:
    repository_id = uuid4()
    run = Run(
        goal="Inspect repository",
        repository_id=repository_id,
        base_commit="a" * 40,
        intent=RunIntent.READ_ONLY,
        dispatch_status=DispatchStatus.QUEUED,
        workspace_path=tmp_path / "workspace",
        integration_branch=f"awesome-agent/run/{uuid4()}",
        workspace_state=WorkspaceState.READY,
        graph_thread_id=f"run:{uuid4()}",
    )

    assert run.repository_id == repository_id
    assert run.intent is RunIntent.READ_ONLY
    assert run.dispatch_status is DispatchStatus.QUEUED
    assert not run.legacy
