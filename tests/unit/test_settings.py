from pathlib import Path

import pytest

from awesome_agent.settings import Settings


def test_settings_use_confirmed_concurrency_defaults() -> None:
    settings = Settings(_env_file=None)

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
    assert settings.artifact_root.parent.name in {".awesome-agent", "awesome-agent"}
    assert settings.team_verifier_model_output_attempts == 2
    assert settings.team_verifier_model_rejection_budget == 10
    assert settings.team_verifier_external_retry_budget == 1
    assert settings.team_verifier_plan_repair_budget == 2
    assert settings.team_patch_conflict_rework_budget == 2
    assert settings.team_model_output_rework_budget == 10
    assert settings.team_default_rework_budget == 1
    assert settings.model_first_event_timeout_seconds == 60.0
    assert settings.model_idle_timeout_seconds == 120.0
    assert settings.model_total_timeout_seconds == 600.0
    assert settings.model_process_shutdown_grace_seconds == 2.0


def test_model_execution_deadline_settings_read_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWESOME_AGENT_MODEL_IDLE_TIMEOUT_SECONDS", "9.5")
    monkeypatch.setenv("AWESOME_AGENT_MODEL_TOTAL_TIMEOUT_SECONDS", "21")
    monkeypatch.setenv("AWESOME_AGENT_MODEL_PROCESS_SHUTDOWN_GRACE_SECONDS", "0.5")

    settings = Settings(_env_file=None)

    assert settings.model_idle_timeout_seconds == 9.5
    assert settings.model_total_timeout_seconds == 21
    assert settings.model_process_shutdown_grace_seconds == 0.5


def test_team_recovery_budget_settings_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="team_verifier_plan_repair_budget"):
        Settings(_env_file=None, team_verifier_plan_repair_budget=0)


def test_settings_load_api_key_from_awesome_env_not_project_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "awesome-home"
    awesome_env = home / ".env"
    awesome_env.parent.mkdir(parents=True)
    awesome_env.write_text(
        "AWESOME_AGENT_DEEPSEEK_API_KEY=user-key\n",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text(
        "AWESOME_AGENT_DEEPSEEK_API_KEY=project-key\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AWESOME_AGENT_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("AWESOME_HOME", str(home))
    monkeypatch.chdir(project)

    settings = Settings()

    assert settings.deepseek_api_key is not None
    assert settings.deepseek_api_key.get_secret_value() == "user-key"


def test_settings_do_not_load_api_key_from_project_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "awesome-home"
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text(
        "AWESOME_AGENT_DEEPSEEK_API_KEY=project-key\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AWESOME_AGENT_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("AWESOME_HOME", str(home))
    monkeypatch.chdir(project)

    settings = Settings()

    assert settings.deepseek_api_key is None


def test_settings_defaults_resolve_from_awesome_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "awesome-home"
    home.mkdir()
    (home / ".env").write_text(
        "AWESOME_AGENT_DEEPSEEK_API_KEY=user-key\n",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text(
        "AWESOME_AGENT_DEEPSEEK_API_KEY=project-key\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AWESOME_AGENT_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("AWESOME_HOME", str(home))
    monkeypatch.chdir(project)

    settings = Settings()

    assert settings.deepseek_api_key is not None
    assert settings.deepseek_api_key.get_secret_value() == "user-key"
    assert settings.artifact_root == home / "runs"
    assert settings.local_config_path == home / "config.toml"
    assert settings.local_state_dir == home / "state"
