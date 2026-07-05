from pathlib import Path

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.surfaces.guidance import (
    OFFICIAL_DEEPSEEK_BASE_URL,
    DoctorStatus,
    build_cli_doctor_report,
    first_run_guidance,
    guidance_for_model_error,
    missing_api_key_guidance,
)


def _summary(
    tmp_path: Path,
    *,
    user_config_exists: bool = False,
    project_config_exists: bool = False,
    project_env_exists: bool = False,
    api_key_configured: bool = False,
    api_key_source: str | None = None,
) -> ConfigFlowSummary:
    return ConfigFlowSummary(
        home=tmp_path,
        project_root=tmp_path / "project",
        user_config=tmp_path / ".awesome-agent" / "config.yaml",
        project_config=tmp_path / "project" / "awesome-agent.yaml",
        project_env=tmp_path / "project" / ".env",
        user_config_exists=user_config_exists,
        project_config_exists=project_config_exists,
        project_env_exists=project_env_exists,
        model_name="deepseek-v4-pro",
        model_api_key_env="AWESOME_AGENT_DEEPSEEK_API_KEY",
        model_api_key_configured=api_key_configured,
        model_api_key_source=api_key_source,
    )


def test_missing_api_key_guidance_is_actionable() -> None:
    guidance = missing_api_key_guidance("AWESOME_AGENT_DEEPSEEK_API_KEY")

    assert guidance.severity == "error"
    assert guidance.title == "API key is missing"
    assert "AWESOME_AGENT_DEEPSEEK_API_KEY" in guidance.detail
    assert guidance.next_steps == (
        "Set AWESOME_AGENT_DEEPSEEK_API_KEY in your environment.",
        "Restart awesome after changing environment variables.",
        "Run: awesome doctor",
    )


def test_model_error_guidance_covers_confirmed_classes() -> None:
    assert guidance_for_model_error("authentication") is not None
    assert guidance_for_model_error("rate_limit") is not None
    assert guidance_for_model_error("transient") is not None
    assert guidance_for_model_error("context_length") is not None
    assert guidance_for_model_error("provider_protocol") is not None
    assert guidance_for_model_error("approval_required") is None


def test_first_run_guidance_reports_only_required_actions(tmp_path: Path) -> None:
    guidance = first_run_guidance(_summary(tmp_path))

    rendered = "\n".join(item.title for item in guidance)
    assert "User config is missing" in rendered
    assert "API key is missing" in rendered
    assert "Project config" not in rendered
    assert "Project env" not in rendered


def test_cli_doctor_report_uses_expected_status_levels(tmp_path: Path) -> None:
    report = build_cli_doctor_report(
        _summary(tmp_path),
        deepseek_base_url=OFFICIAL_DEEPSEEK_BASE_URL,
    )

    statuses = [line.status for line in report.lines]
    rendered = report.render()
    assert DoctorStatus.WARN in statuses
    assert DoctorStatus.ERROR in statuses
    assert "WARN  User config:" in rendered
    assert "ERROR API key:" in rendered
    assert "INFO  Project config:" in rendered
    assert "INFO  Project env:" in rendered
    assert report.exit_code == 1


def test_cli_doctor_report_rejects_custom_base_url(tmp_path: Path) -> None:
    report = build_cli_doctor_report(
        _summary(tmp_path, user_config_exists=True, api_key_configured=True),
        deepseek_base_url="https://example.test",
    )

    rendered = report.render()
    assert "ERROR Base URL:" in rendered
    assert "https://api.deepseek.com" in rendered
    assert report.exit_code == 1


def test_cli_doctor_report_accepts_settings_configured_api_key(
    tmp_path: Path,
) -> None:
    report = build_cli_doctor_report(
        _summary(
            tmp_path,
            user_config_exists=True,
            api_key_configured=True,
            api_key_source="settings",
        ),
        deepseek_base_url=OFFICIAL_DEEPSEEK_BASE_URL,
    )

    rendered = report.render()
    assert (
        "OK    API key: AWESOME_AGENT_DEEPSEEK_API_KEY is configured through Settings"
        in rendered
    )
    assert "ERROR API key" not in rendered
    assert report.exit_code == 0
