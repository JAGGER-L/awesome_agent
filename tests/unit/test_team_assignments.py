from uuid import uuid4

import pytest

from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    validate_assignment_graph,
)


def test_teammate_assignment_uses_team_role_graph() -> None:
    assignment = TeamAssignment(
        root_run_id=uuid4(),
        parent_run_id=uuid4(),
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="backend-engineer",
        graph_name="team-role",
        graph_version=1,
        goal="Implement backend",
        allowed_tools=["repo.read", "repo.apply_patch"],
        allowed_skills=["patch-authoring"],
        can_write=True,
        can_delegate=True,
        max_subagents=3,
        acceptance_criteria=["Verifier must pass."],
    )

    assert validate_assignment_graph(assignment)


def test_verifier_assignment_uses_verifier_graph() -> None:
    assignment = TeamAssignment(
        root_run_id=uuid4(),
        parent_run_id=uuid4(),
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.VERIFIER,
        role_profile="verifier",
        graph_name="team-verifier",
        graph_version=1,
        goal="Verify aggregation",
    )

    assert validate_assignment_graph(assignment)


def test_subagent_assignment_cannot_delegate() -> None:
    assignment = TeamAssignment(
        root_run_id=uuid4(),
        parent_run_id=uuid4(),
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.SUBAGENT,
        role_profile="reader",
        graph_name="team-role",
        graph_version=1,
        goal="Read one file",
        can_delegate=True,
        max_subagents=1,
    )

    with pytest.raises(ValueError, match="subagent assignments cannot delegate"):
        validate_assignment_graph(assignment)
