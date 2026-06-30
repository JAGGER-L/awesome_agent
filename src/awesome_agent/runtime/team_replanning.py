from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from awesome_agent.domain.enums import RunIntent
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamChildResult,
)
from awesome_agent.runtime.team_planning import (
    TeamPlan,
    TeamPlanTeammate,
    validate_team_plan_for_intent,
)
from awesome_agent.runtime.team_rework import patch_conflict_superseded_child_ids

PLAN_REPAIR_REASON_VERIFIER_REWORK = "verifier_rework"
PLAN_REPAIR_SUPERSEDED_REASONS = frozenset({PLAN_REPAIR_REASON_VERIFIER_REWORK})
_PLAN_REPAIR_BUDGETS = {PLAN_REPAIR_REASON_VERIFIER_REWORK: 2}
_MAX_EFFECTIVE_TEAMMATES = 6


class TeamPlanRepairActionKind(StrEnum):
    ADD_TEAMMATE = "add_teammate"
    REPLACE_TEAMMATE = "replace_teammate"


class TeamPlanRepairAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: TeamPlanRepairActionKind
    reason: str = Field(min_length=1, max_length=2000)
    teammate: TeamPlanTeammate
    target_child_run_id: str | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_target(self) -> TeamPlanRepairAction:
        if (
            self.action is TeamPlanRepairActionKind.REPLACE_TEAMMATE
            and not self.target_child_run_id
        ):
            raise ValueError("replace_teammate requires target_child_run_id")
        if (
            self.action is TeamPlanRepairActionKind.ADD_TEAMMATE
            and self.target_child_run_id is not None
        ):
            raise ValueError("add_teammate cannot target an existing child")
        return self


class TeamPlanRepair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1, max_length=4000)
    actions: list[TeamPlanRepairAction] = Field(min_length=1, max_length=3)


def validate_team_plan_repair(
    repair: TeamPlanRepair,
    *,
    intent: RunIntent,
    assignments: list[TeamAssignment],
) -> TeamPlanRepair:
    targetable = {
        str(assignment.child_run_id): assignment
        for assignment in assignments
        if assignment.kind is TeamAssignmentKind.TEAMMATE
    }
    targets_seen: set[str] = set()
    for action in repair.actions:
        if action.target_child_run_id is None:
            continue
        if action.target_child_run_id not in targetable:
            raise ValueError("repair target must be a current teammate")
        if action.target_child_run_id in targets_seen:
            raise ValueError("repair target cannot be replaced twice")
        targets_seen.add(action.target_child_run_id)
    validate_team_plan_for_intent(
        TeamPlan(
            rationale=repair.rationale,
            teammates=[action.teammate for action in repair.actions],
        ),
        intent=intent,
    )
    effective_count = _effective_teammate_count_after_repair(assignments, repair)
    if effective_count > _MAX_EFFECTIVE_TEAMMATES:
        raise ValueError("team repair would exceed maximum teammate count")
    return repair


def plan_repair_budget_for_reason(reason: str) -> int:
    return _PLAN_REPAIR_BUDGETS.get(reason, 1)


def plan_repair_attempt_for_verifier(
    assignments: list[TeamAssignment],
    *,
    verifier_child_run_id: UUID,
) -> int:
    verifier_id = str(verifier_child_run_id)
    return (
        sum(
            1
            for assignment in assignments
            if assignment.handoff_context.get("plan_repair_reason")
            == PLAN_REPAIR_REASON_VERIFIER_REWORK
            and assignment.handoff_context.get("plan_repair_verifier_child_run_id")
            == verifier_id
        )
        + 1
    )


def plan_repair_superseded_child_ids(
    assignments: list[TeamAssignment],
) -> set[str]:
    return {
        str(previous_child_run_id)
        for assignment in assignments
        if assignment.handoff_context.get("plan_repair_reason")
        in PLAN_REPAIR_SUPERSEDED_REASONS
        and assignment.handoff_context.get("plan_repair_action")
        == TeamPlanRepairActionKind.REPLACE_TEAMMATE.value
        for previous_child_run_id in [
            assignment.handoff_context.get("previous_child_run_id")
        ]
        if previous_child_run_id is not None
    }


def superseded_child_ids_for_team_verification(
    assignments: list[TeamAssignment],
) -> set[str]:
    return patch_conflict_superseded_child_ids(
        assignments
    ) | plan_repair_superseded_child_ids(assignments)


def effective_child_results_for_team_verification(
    results: list[TeamChildResult],
    assignments: list[TeamAssignment],
) -> list[TeamChildResult]:
    superseded = superseded_child_ids_for_team_verification(assignments)
    return [result for result in results if str(result.child_run_id) not in superseded]


def _effective_teammate_count_after_repair(
    assignments: list[TeamAssignment],
    repair: TeamPlanRepair,
) -> int:
    current_child_ids = {
        str(assignment.child_run_id)
        for assignment in assignments
        if assignment.kind is TeamAssignmentKind.TEAMMATE
    }
    replaced_child_ids = {
        action.target_child_run_id
        for action in repair.actions
        if action.action is TeamPlanRepairActionKind.REPLACE_TEAMMATE
        and action.target_child_run_id is not None
    }
    return len(current_child_ids - replaced_child_ids) + len(repair.actions)
