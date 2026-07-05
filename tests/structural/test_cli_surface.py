from pathlib import Path

from awesome_agent.cli.slash_commands import (
    SlashCommandKind,
    command_suggestions,
    parse_slash_command,
    slash_command_help,
)

REMOVED_RUN_COMMAND = "/" + "run"

RETAINED_COMMANDS = [
    "/help",
    "/new",
    "/threads",
    "/attach",
    "/model",
    "/thinking",
    "/memory",
    "/skills",
    "/tools",
    "/mcp",
    "/status",
    "/usage",
    "/config",
    "/details",
    "/quit",
]

DELETED_COMMANDS = [
    "/resume",
    "/models",
    "/uploads",
    "/artifacts",
    "/switch",
    REMOVED_RUN_COMMAND,
]


def test_awesome_script_is_declared() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'awesome = "awesome_agent.cli.interactive:main"' in pyproject
    assert 'awesome-agent = "awesome_agent.cli.app:app"' in pyproject


def test_interactive_cli_documents_required_slash_commands() -> None:
    text = Path("docs/user-guide/cli.md").read_text(encoding="utf-8")

    for command in RETAINED_COMMANDS:
        assert command in text
    for command in DELETED_COMMANDS:
        assert command not in text


def test_readmes_link_to_cli_command_inventory() -> None:
    for path in ["README.md", "README.zh-CN.md"]:
        text = Path(path).read_text(encoding="utf-8")
        assert "docs/user-guide/README.md" in text
        for command in DELETED_COMMANDS:
            assert f"| `{command}` |" not in text


def test_slash_command_registry_matches_final_inventory() -> None:
    suggestions = {f"/{item.name}" for item in command_suggestions("/")}

    assert suggestions == set(RETAINED_COMMANDS)
    for command in RETAINED_COMMANDS:
        assert command in slash_command_help()
    for command in DELETED_COMMANDS:
        assert parse_slash_command(command).kind is SlashCommandKind.UNKNOWN
        assert command not in slash_command_help()
