import pytest

from awesome_agent.runtime.team_recovery_policy import (
    PATCH_CONFLICT_REWORK_REASON,
    PLAN_REPAIR_REASON_VERIFIER_REWORK,
    TeamRecoveryPolicy,
)


def test_default_policy_preserves_existing_budgets() -> None:
    policy = TeamRecoveryPolicy()

    assert policy.verifier_model_output_attempts == 2
    assert policy.verifier_model_rejection_budget == 10
    assert policy.verifier_external_retry_budget == 1
    assert policy.plan_repair_budget(PLAN_REPAIR_REASON_VERIFIER_REWORK) == 2
    assert policy.rework_budget_for_failure(PATCH_CONFLICT_REWORK_REASON) == 2
    assert policy.rework_budget_for_failure("model_output_failure") == 10
    assert policy.rework_budget_for_failure("unknown_failure") == 1


def test_policy_override_changes_specific_failure_budgets() -> None:
    policy = TeamRecoveryPolicy(
        verifier_plan_repair_budget=3,
        patch_conflict_rework_budget=4,
        model_output_rework_budget=5,
        default_rework_budget=2,
    )

    assert policy.plan_repair_budget(PLAN_REPAIR_REASON_VERIFIER_REWORK) == 3
    assert policy.rework_budget_for_failure(PATCH_CONFLICT_REWORK_REASON) == 4
    assert policy.rework_budget_for_failure("model_output_failure") == 5
    assert policy.rework_budget_for_failure("other_failure") == 2


def test_policy_rejects_zero_or_negative_budgets() -> None:
    with pytest.raises(ValueError, match="verifier_model_output_attempts"):
        TeamRecoveryPolicy(verifier_model_output_attempts=0)
    with pytest.raises(ValueError, match="verifier_plan_repair_budget"):
        TeamRecoveryPolicy(verifier_plan_repair_budget=0)
    with pytest.raises(ValueError, match="patch_conflict_rework_budget"):
        TeamRecoveryPolicy(patch_conflict_rework_budget=0)
