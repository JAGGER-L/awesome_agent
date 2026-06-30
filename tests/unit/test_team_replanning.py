from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from awesome_agent.domain.enums import RunIntent
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
    TeamChildResult,
)
from awesome_agent.runtime.team_planning import TeamPlanTeammate
from awesome_agent.runtime.team_recovery_policy import (
    PATCH_CONFLICT_REWORK_REASON,
    PLAN_REPAIR_REASON_VERIFIER_REWORK,
    TeamRecoveryPolicy,
)
from awesome_agent.runtime.team_replanning import (
    PLAN_REPAIR_SUPERSEDED_REASONS,
    TeamPlanRepair,
    TeamPlanRepairAction,
    TeamPlanRepairActionKind,
    effective_child_results_for_team_verification,
    plan_repair_attempt_for_verifier,
    plan_repair_budget_for_reason,
    superseded_child_ids_for_team_verification,
    validate_team_plan_repair,
)


def test_repair_action_replace_requires_target_child_run_id() -> None:
    with pytest.raises(
        ValidationError, match="replace_teammate requires target_child_run_id"
    ):
        TeamPlanRepairAction(
            action=TeamPlanRepairActionKind.REPLACE_TEAMMATE,
            reason="Missing evidence.",
            teammate=_teammate_plan(),
        )


def test_repair_action_add_forbids_target_child_run_id() -> None:
    with pytest.raises(
        ValidationError, match="add_teammate cannot target an existing child"
    ):
        TeamPlanRepairAction(
            action=TeamPlanRepairActionKind.ADD_TEAMMATE,
            target_child_run_id=str(uuid4()),
            reason="Split the role.",
            teammate=_teammate_plan(),
        )


def test_validate_repair_rejects_unknown_replacement_target() -> None:
    repair = TeamPlanRepair(
        rationale="Replace missing evidence.",
        actions=[
            TeamPlanRepairAction(
                action=TeamPlanRepairActionKind.REPLACE_TEAMMATE,
                target_child_run_id=str(uuid4()),
                reason="Missing README evidence.",
                teammate=_teammate_plan(),
            )
        ],
    )

    with pytest.raises(ValueError, match="repair target must be a current teammate"):
        validate_team_plan_repair(
            repair,
            intent=RunIntent.MODIFYING,
            assignments=[_assignment()],
        )


def test_validate_repair_uses_team_plan_tool_rules_for_read_only_runs() -> None:
    target = _assignment()
    repair = TeamPlanRepair(
        rationale="Replace with invalid writer.",
        actions=[
            TeamPlanRepairAction(
                action=TeamPlanRepairActionKind.REPLACE_TEAMMATE,
                target_child_run_id=str(target.child_run_id),
                reason="Should not write in read-only mode.",
                teammate=_teammate_plan(
                    allowed_tools=["repo.read", "repo.apply_patch"],
                    can_write=True,
                ),
            )
        ],
    )

    with pytest.raises(
        ValueError, match="read-only team plans cannot create writing teammates"
    ):
        validate_team_plan_repair(
            repair,
            intent=RunIntent.READ_ONLY,
            assignments=[target],
        )


def test_plan_repair_attempt_counts_prior_verifier_repair_assignments() -> None:
    verifier_child_run_id = uuid4()
    original = _assignment()
    first_repair = _assignment(
        handoff_context={
            "plan_repair_reason": PLAN_REPAIR_REASON_VERIFIER_REWORK,
            "plan_repair_verifier_child_run_id": str(verifier_child_run_id),
            "previous_assignment_id": str(original.id),
            "previous_child_run_id": str(original.child_run_id),
            "plan_repair_attempt": 1,
        }
    )

    assert (
        plan_repair_attempt_for_verifier(
            [original, first_repair],
            verifier_child_run_id=verifier_child_run_id,
        )
        == 2
    )
    assert plan_repair_budget_for_reason(PLAN_REPAIR_REASON_VERIFIER_REWORK) == 2


def test_plan_repair_budget_accepts_policy_override() -> None:
    policy = TeamRecoveryPolicy(verifier_plan_repair_budget=4)

    assert (
        plan_repair_budget_for_reason(
            PLAN_REPAIR_REASON_VERIFIER_REWORK,
            policy=policy,
        )
        == 4
    )


def test_effective_results_filter_superseded_children() -> None:
    root_run_id = uuid4()
    parent_run_id = uuid4()
    patch_target = _assignment(root_run_id=root_run_id, parent_run_id=parent_run_id)
    repair_target = _assignment(root_run_id=root_run_id, parent_run_id=parent_run_id)
    replacement = _assignment(
        root_run_id=root_run_id,
        parent_run_id=parent_run_id,
        handoff_context={
            "plan_repair_reason": PLAN_REPAIR_REASON_VERIFIER_REWORK,
            "plan_repair_action": "replace_teammate",
            "previous_assignment_id": str(repair_target.id),
            "previous_child_run_id": str(repair_target.child_run_id),
            "plan_repair_attempt": 1,
        },
    )
    patch_replacement = _assignment(
        root_run_id=root_run_id,
        parent_run_id=parent_run_id,
        handoff_context={
            "rework_reason": PATCH_CONFLICT_REWORK_REASON,
            "previous_assignment_id": str(patch_target.id),
            "previous_child_run_id": str(patch_target.child_run_id),
            "rework_attempt": 1,
        },
    )
    keep = _assignment(root_run_id=root_run_id, parent_run_id=parent_run_id)
    results = [
        _result(patch_target),
        _result(repair_target),
        _result(replacement),
        _result(patch_replacement),
        _result(keep),
    ]

    superseded = superseded_child_ids_for_team_verification(
        [patch_target, repair_target, replacement, patch_replacement, keep]
    )
    effective = effective_child_results_for_team_verification(
        results,
        [patch_target, repair_target, replacement, patch_replacement, keep],
    )

    assert str(patch_target.child_run_id) in superseded
    assert str(repair_target.child_run_id) in superseded
    assert PLAN_REPAIR_REASON_VERIFIER_REWORK in PLAN_REPAIR_SUPERSEDED_REASONS
    assert [item.child_run_id for item in effective] == [
        replacement.child_run_id,
        patch_replacement.child_run_id,
        keep.child_run_id,
    ]


def _teammate_plan(
    *,
    allowed_tools: list[str] | None = None,
    can_write: bool = False,
) -> TeamPlanTeammate:
    return TeamPlanTeammate(
        role_profile="backend-engineer",
        goal="Repair the missing evidence.",
        allowed_tools=allowed_tools or ["repo.read"],
        deferred_tools=[],
        allowed_skills=["python"],
        can_write=can_write,
        can_delegate=False,
        max_subagents=0,
        acceptance_criteria=["Return repaired evidence."],
    )


def _assignment(
    *,
    root_run_id: UUID | None = None,
    parent_run_id: UUID | None = None,
    handoff_context: dict[str, object] | None = None,
) -> TeamAssignment:
    root = root_run_id or uuid4()
    return TeamAssignment(
        root_run_id=root,
        parent_run_id=parent_run_id or root,
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="Inspect README.",
        allowed_tools=["repo.read"],
        acceptance_criteria=["Return README evidence."],
        handoff_context=handoff_context or {},
    )


def _result(assignment: TeamAssignment) -> TeamChildResult:
    return TeamChildResult(
        assignment_id=assignment.id,
        child_run_id=assignment.child_run_id,
        parent_run_id=assignment.parent_run_id,
        root_run_id=assignment.root_run_id,
        status="completed",
        summary=f"result {assignment.child_run_id}",
        patch_aggregated=True,
    )
