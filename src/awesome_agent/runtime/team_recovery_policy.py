from __future__ import annotations

from dataclasses import dataclass, fields

PATCH_CONFLICT_REWORK_REASON = "patch_conflict"
PLAN_REPAIR_REASON_VERIFIER_REWORK = "verifier_rework"


@dataclass(frozen=True, slots=True)
class TeamRecoveryPolicy:
    verifier_model_output_attempts: int = 2
    verifier_model_rejection_budget: int = 10
    verifier_external_retry_budget: int = 1
    verifier_plan_repair_budget: int = 2
    patch_conflict_rework_budget: int = 2
    model_output_rework_budget: int = 10
    default_rework_budget: int = 1

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if value < 1:
                raise ValueError(f"{field.name} must be at least 1")

    def plan_repair_budget(self, reason: str) -> int:
        if reason == PLAN_REPAIR_REASON_VERIFIER_REWORK:
            return self.verifier_plan_repair_budget
        return self.default_rework_budget

    def rework_budget_for_failure(self, failure_kind: str | None) -> int:
        if failure_kind == PATCH_CONFLICT_REWORK_REASON:
            return self.patch_conflict_rework_budget
        if failure_kind in {None, "rework_required", "model_output_failure"}:
            return self.model_output_rework_budget
        return self.default_rework_budget
