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
