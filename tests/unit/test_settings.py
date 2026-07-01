import pytest

from awesome_agent.settings import Settings


def test_settings_use_confirmed_concurrency_defaults() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.max_teammates == 6
    assert settings.max_subagents_per_teammate == 3
    assert settings.max_model_concurrency == 8
    assert settings.max_tool_concurrency == 12
    assert settings.max_sandbox_concurrency == 6
    assert not settings.builtin_memory_enabled
    assert not settings.mem0_enabled
    assert settings.leader_model == "deepseek-v4-pro"
    assert settings.teammate_model == "deepseek-v4-flash"
    assert settings.verifier_model == "deepseek-v4-flash"
    assert settings.subagent_model == "deepseek-v4-flash"
    assert settings.deepseek_thinking_enabled
    assert settings.observability_enabled is True
    assert settings.otel_service_name == "awesome-agent"
    assert settings.otel_console_exporter_enabled is True
    assert settings.otel_otlp_endpoint is None
    assert settings.artifact_root.name == "runs"
    assert settings.artifact_root.parent.name == ".awesome-agent"
    assert settings.team_verifier_model_output_attempts == 2
    assert settings.team_verifier_model_rejection_budget == 10
    assert settings.team_verifier_external_retry_budget == 1
    assert settings.team_verifier_plan_repair_budget == 2
    assert settings.team_patch_conflict_rework_budget == 2
    assert settings.team_model_output_rework_budget == 10
    assert settings.team_default_rework_budget == 1


def test_team_recovery_budget_settings_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="team_verifier_plan_repair_budget"):
        Settings(_env_file=None, team_verifier_plan_repair_budget=0)  # type: ignore[call-arg]
