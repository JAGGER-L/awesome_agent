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
    assert "/models" in result.output
    assert "/memory" in result.output
    assert "/help" in result.output


def test_awesome_init_creates_user_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / ".awesome-agent" / "config.yaml").exists()
    assert "Created or verified" in result.output
    assert "AWESOME_AGENT_DEEPSEEK_API_KEY" in result.output


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
