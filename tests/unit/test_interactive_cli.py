from pathlib import Path

import pytest
from typer.testing import CliRunner

from awesome_agent.cli.interactive import app
from awesome_agent.cli.profile import local_cli_profile

runner = CliRunner()


def test_local_cli_profile_defaults_to_local_sandbox() -> None:
    profile = local_cli_profile()

    assert profile.name == "local-cli"
    assert profile.default_sandbox_backend == "local"
    assert profile.requires_api_before_launch is False


def test_awesome_can_print_help_without_api() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "interactive local coding-agent CLI" in result.output


def test_awesome_commands_lists_slash_commands() -> None:
    result = runner.invoke(app, ["commands"])

    assert result.exit_code == 0
    assert "/new" in result.output
    assert "/status" in result.output
    assert "/model" in result.output
    assert "/thinking" in result.output
    assert "/memory" in result.output
    assert "/help" in result.output
    assert "/models" not in result.output


def test_awesome_init_creates_user_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / ".awesome-agent" / "config.yaml").exists()
    assert "Awesome Agent initialized" in result.output
    assert (
        f"OK    User config: {tmp_path / '.awesome-agent' / 'config.yaml'}"
        in result.output
    )
    assert "Next:" in result.output
    assert "Set AWESOME_AGENT_DEEPSEEK_API_KEY in your environment." in result.output
    assert "Run: awesome doctor" in result.output
    assert "Start: cd <project>; awesome" in result.output


def test_awesome_doctor_reports_missing_key_and_exits_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("AWESOME_AGENT_DEEPSEEK_API_KEY", raising=False)

    result = runner.invoke(app, ["doctor", "--project-root", str(tmp_path)])

    assert result.exit_code == 1
    assert "Awesome Agent CLI check" in result.output
    assert "WARN  User config:" in result.output
    assert "ERROR API key: AWESOME_AGENT_DEEPSEEK_API_KEY is missing" in result.output
    assert "INFO  Project config:" in result.output
    assert "INFO  Project env:" in result.output
    assert "Runtime:" not in result.output
    assert "Worker:" not in result.output
    assert "Docker" not in result.output
    assert "Postgres" not in result.output


def test_awesome_doctor_exits_zero_with_key_and_official_base_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("AWESOME_AGENT_DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("AWESOME_AGENT_DEEPSEEK_BASE_URL", raising=False)
    (tmp_path / ".awesome-agent").mkdir()
    (tmp_path / ".awesome-agent" / "config.yaml").write_text(
        "version: 1\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["doctor", "--project-root", str(tmp_path)])

    assert result.exit_code == 0
    assert "OK    User config:" in result.output
    assert "OK    API key: AWESOME_AGENT_DEEPSEEK_API_KEY is set" in result.output
    assert "OK    Base URL: https://api.deepseek.com" in result.output
    assert "Start: awesome" in result.output


def test_awesome_doctor_rejects_custom_deepseek_base_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("AWESOME_AGENT_DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("AWESOME_AGENT_DEEPSEEK_BASE_URL", "https://example.test")
    (tmp_path / ".awesome-agent").mkdir()
    (tmp_path / ".awesome-agent" / "config.yaml").write_text(
        "version: 1\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["doctor", "--project-root", str(tmp_path)])

    assert result.exit_code == 1
    assert "ERROR Base URL: https://example.test is unsupported" in result.output
    assert "https://api.deepseek.com" in result.output


def test_awesome_launches_chat_tui(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: dict[str, object] = {}

    class FakeTui:
        def __init__(
            self,
            *,
            api_url: str,
            run_id: str | None = None,
            launch_context: object | None = None,
            first_run_summary: object | None = None,
        ) -> None:
            launched["api_url"] = api_url
            launched["run_id"] = run_id
            launched["launch_context"] = launch_context
            launched["first_run_summary"] = first_run_summary

        def run(self) -> None:
            launched["ran"] = True

    monkeypatch.setattr("awesome_agent.cli.interactive.AwesomeAgentTui", FakeTui)

    result = runner.invoke(app, ["--api-url", "http://127.0.0.1:9000"])

    assert result.exit_code == 0
    assert launched == {
        "api_url": "http://127.0.0.1:9000",
        "run_id": None,
        "launch_context": launched["launch_context"],
        "first_run_summary": launched["first_run_summary"],
        "ran": True,
    }
    assert launched["launch_context"] is not None
    assert launched["first_run_summary"] is not None


def test_awesome_defaults_to_embedded_local_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: dict[str, object] = {}

    class FakeTui:
        def __init__(
            self,
            *,
            api_url: str | None = None,
            run_id: str | None = None,
            launch_context: object | None = None,
            first_run_summary: object | None = None,
        ) -> None:
            launched["api_url"] = api_url
            launched["run_id"] = run_id
            launched["launch_context"] = launch_context
            launched["first_run_summary"] = first_run_summary

        def run(self) -> None:
            launched["ran"] = True

    monkeypatch.setattr("awesome_agent.cli.interactive.AwesomeAgentTui", FakeTui)

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert launched["api_url"] is None
    assert launched["run_id"] is None
    assert launched["ran"] is True
    assert result.output == ""
