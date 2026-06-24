from uuid import uuid4

from awesome_agent.domain.enums import AgentKind, EventType
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
