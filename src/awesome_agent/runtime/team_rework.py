from __future__ import annotations

import json

from awesome_agent.runtime.team_verification import TeamVerificationDecision

REWORK_DECISION_PREFIX = "TEAM_REWORK_DECISION:"


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
