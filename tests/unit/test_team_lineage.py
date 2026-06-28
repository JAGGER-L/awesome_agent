from uuid import uuid4

import pytest

from awesome_agent.domain.enums import RunMode
from awesome_agent.domain.models import Run
from awesome_agent.runtime.team_assignments import validate_child_depth


def test_run_lineage_allows_leader_teammate_and_subagent_depths() -> None:
    root_id = uuid4()
    leader = Run(goal="team", mode=RunMode.TEAM, root_run_id=root_id, depth=0)
    teammate = Run(
        goal="backend",
        mode=RunMode.TEAM,
        parent_run_id=leader.id,
        root_run_id=root_id,
        depth=1,
        child_role="teammate",
    )
    subagent = Run(
        goal="read README",
        mode=RunMode.TEAM,
        parent_run_id=teammate.id,
        root_run_id=root_id,
        depth=2,
        child_role="subagent",
    )

    assert validate_child_depth(leader, teammate)
    assert validate_child_depth(teammate, subagent)


def test_depth_greater_than_two_is_rejected() -> None:
    parent = Run(goal="subagent", mode=RunMode.TEAM, depth=2)

    with pytest.raises(ValueError, match="less than or equal to 2"):
        Run(
            goal="too deep",
            mode=RunMode.TEAM,
            parent_run_id=parent.id,
            root_run_id=parent.root_run_id or parent.id,
            depth=3,
            child_role="subagent",
        )
