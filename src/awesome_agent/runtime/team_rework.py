from __future__ import annotations

import json

from awesome_agent.runtime.team_assignments import TeamAssignment, TeamChildResult
from awesome_agent.runtime.team_verification import TeamVerificationDecision

REWORK_DECISION_PREFIX = "TEAM_REWORK_DECISION:"
PATCH_CONFLICT_REWORK_REASON = "patch_conflict"


def encode_rework_decision(decision: TeamVerificationDecision) -> str:
    return f"{REWORK_DECISION_PREFIX}{decision.model_dump_json()}"


def decode_rework_decision(summary: str) -> TeamVerificationDecision | None:
    if not summary.startswith(REWORK_DECISION_PREFIX):
        return None
    raw = json.loads(summary.removeprefix(REWORK_DECISION_PREFIX))
    return TeamVerificationDecision.model_validate(raw)


def rework_budget_for_failure(failure_kind: str | None) -> int:
    if failure_kind in {None, "rework_required", "model_output_failure"}:
        return 10
    if failure_kind == PATCH_CONFLICT_REWORK_REASON:
        return 2
    return 1


def compose_rework_goal(
    *,
    original_goal: str,
    feedback_summary: str,
    acceptance_criteria: list[str],
) -> str:
    criteria = "\n".join(f"- {item}" for item in acceptance_criteria)
    return (
        f"Rework the previous teammate attempt.\n\n"
        f"Original goal:\n{original_goal}\n\n"
        f"Verifier feedback:\n{feedback_summary}\n\n"
        f"Replacement acceptance criteria:\n{criteria}"
    )


def assignment_lineage_id(assignment: TeamAssignment) -> str:
    return str(
        assignment.handoff_context.get("previous_assignment_id") or assignment.id
    )


def rework_attempt_for_lineage(
    assignments: list[TeamAssignment],
    *,
    lineage_id: str,
) -> int:
    return (
        sum(
            1
            for assignment in assignments
            if str(assignment.handoff_context.get("previous_assignment_id"))
            == lineage_id
        )
        + 1
    )


def patch_conflict_replacement_exists(
    assignments: list[TeamAssignment],
    *,
    lineage_id: str,
    source_child_run_id: object,
) -> bool:
    return any(
        str(assignment.handoff_context.get("previous_assignment_id")) == lineage_id
        and assignment.handoff_context.get("rework_reason")
        == PATCH_CONFLICT_REWORK_REASON
        and assignment.handoff_context.get("previous_child_run_id")
        == str(source_child_run_id)
        for assignment in assignments
    )


def patch_conflict_superseded_child_ids(
    assignments: list[TeamAssignment],
) -> set[str]:
    return {
        str(previous_child_run_id)
        for assignment in assignments
        if assignment.handoff_context.get("rework_reason")
        == PATCH_CONFLICT_REWORK_REASON
        for previous_child_run_id in [
            assignment.handoff_context.get("previous_child_run_id")
        ]
        if previous_child_run_id is not None
    }


def effective_child_results_for_verification(
    results: list[TeamChildResult],
    assignments: list[TeamAssignment],
) -> list[TeamChildResult]:
    from awesome_agent.runtime.team_replanning import (
        effective_child_results_for_team_verification,
    )

    return effective_child_results_for_team_verification(results, assignments)


def compose_patch_conflict_rework_goal(
    *,
    original_goal: str,
    conflict_summary: str,
    acceptance_criteria: list[str],
) -> str:
    criteria = "\n".join(f"- {item}" for item in acceptance_criteria)
    return (
        "Rework the previous teammate attempt after Leader patch aggregation "
        "detected a conflict.\n\n"
        f"Original goal:\n{original_goal}\n\n"
        "Patch conflict detected while applying the previous patch artifact to "
        "the current root workspace:\n"
        f"{conflict_summary}\n\n"
        "Produce a new patch against the current root workspace state, preserve "
        "already aggregated teammate changes, call repo.diff after writing, and "
        "satisfy these acceptance criteria:\n"
        f"{criteria}"
    )
