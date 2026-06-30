from uuid import uuid4

import pytest

from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamChildResult,
    effective_assignment_tools,
    validate_assignment_graph,
)
from awesome_agent.runtime.team_recovery_policy import (
    PATCH_CONFLICT_REWORK_REASON,
    TeamRecoveryPolicy,
)
from awesome_agent.runtime.team_rework import (
    effective_child_results_for_verification,
    patch_conflict_superseded_child_ids,
    rework_budget_for_failure,
)


def test_teammate_assignment_uses_TEAM_ROLE_ROUTE() -> None:
    assignment = TeamAssignment(
        root_run_id=uuid4(),
        parent_run_id=uuid4(),
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="Implement backend",
        allowed_tools=["repo.read", "repo.apply_patch"],
        allowed_skills=["patch-authoring"],
        can_write=True,
        can_delegate=True,
        max_subagents=3,
        acceptance_criteria=["Verifier must pass."],
    )

    assert validate_assignment_graph(assignment)
    assert not hasattr(assignment, "graph_version")


def test_verifier_assignment_uses_verifier_graph() -> None:
    assignment = TeamAssignment(
        root_run_id=uuid4(),
        parent_run_id=uuid4(),
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.VERIFIER,
        role_profile="verifier",
        runtime_route="team-verifier",
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
        runtime_route="team-role",
        goal="Read one file",
        can_delegate=True,
        max_subagents=1,
    )

    with pytest.raises(ValueError, match="subagent assignments cannot delegate"):
        validate_assignment_graph(assignment)


def test_effective_assignment_tools_hide_deferred_until_promoted() -> None:
    assignment = TeamAssignment(
        root_run_id=uuid4(),
        parent_run_id=uuid4(),
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="Implement backend",
        allowed_tools=["repo.read", "repo.apply_patch", "shell.execute"],
        deferred_tools=["repo.apply_patch", "shell.execute"],
        promoted_tools=["repo.apply_patch", "not-granted"],
        can_write=True,
    )

    assert effective_assignment_tools(assignment) == ["repo.read", "repo.apply_patch"]


def test_patch_conflict_superseded_child_ids_uses_rework_reason() -> None:
    original = TeamAssignment(
        root_run_id=uuid4(),
        parent_run_id=uuid4(),
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="backend",
        runtime_route="team-role",
        goal="original",
    )
    replacement = TeamAssignment(
        root_run_id=original.root_run_id,
        parent_run_id=original.parent_run_id,
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="backend",
        runtime_route="team-role",
        goal="replacement",
        handoff_context={
            "rework_reason": PATCH_CONFLICT_REWORK_REASON,
            "previous_assignment_id": str(original.id),
            "previous_child_run_id": str(original.child_run_id),
        },
    )

    assert patch_conflict_superseded_child_ids([original, replacement]) == {
        str(original.child_run_id)
    }


def test_effective_child_results_excludes_patch_conflict_superseded_result() -> None:
    parent_run_id = uuid4()
    root_run_id = parent_run_id
    original = TeamAssignment(
        root_run_id=root_run_id,
        parent_run_id=parent_run_id,
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="backend",
        runtime_route="team-role",
        goal="original",
    )
    replacement = TeamAssignment(
        root_run_id=root_run_id,
        parent_run_id=parent_run_id,
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="backend",
        runtime_route="team-role",
        goal="replacement",
        handoff_context={
            "rework_reason": PATCH_CONFLICT_REWORK_REASON,
            "previous_assignment_id": str(original.id),
            "previous_child_run_id": str(original.child_run_id),
        },
    )
    original_result = TeamChildResult(
        assignment_id=original.id,
        child_run_id=original.child_run_id,
        parent_run_id=parent_run_id,
        root_run_id=root_run_id,
        status="recovery_required",
        summary="patch conflict",
        patch_artifact_id=uuid4(),
        failure_kind=PATCH_CONFLICT_REWORK_REASON,
    )
    replacement_result = TeamChildResult(
        assignment_id=replacement.id,
        child_run_id=replacement.child_run_id,
        parent_run_id=parent_run_id,
        root_run_id=root_run_id,
        status="completed",
        summary="replacement patch",
        patch_artifact_id=uuid4(),
        patch_aggregated=True,
    )

    assert effective_child_results_for_verification(
        [original_result, replacement_result],
        [original, replacement],
    ) == [replacement_result]


def test_patch_conflict_rework_budget_allows_two_attempts() -> None:
    assert rework_budget_for_failure(PATCH_CONFLICT_REWORK_REASON) == 2


def test_rework_budget_accepts_policy_override() -> None:
    policy = TeamRecoveryPolicy(
        patch_conflict_rework_budget=3,
        model_output_rework_budget=6,
        default_rework_budget=2,
    )

    assert rework_budget_for_failure(PATCH_CONFLICT_REWORK_REASON, policy=policy) == 3
    assert rework_budget_for_failure("model_output_failure", policy=policy) == 6
    assert rework_budget_for_failure("unknown_failure", policy=policy) == 2
