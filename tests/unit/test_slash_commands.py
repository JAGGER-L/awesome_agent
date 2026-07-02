from awesome_agent.cli.slash_commands import (
    SlashCommandKind,
    command_suggestions,
    parse_slash_command,
    slash_command_help,
)


def test_parse_known_command_with_argument() -> None:
    command = parse_slash_command("/run build a game")

    assert command.kind is SlashCommandKind.RUN
    assert command.argument == "build a game"


def test_parse_model_command() -> None:
    command = parse_slash_command("/model")

    assert command.kind is SlashCommandKind.MODEL


def test_deleted_commands_parse_as_unknown() -> None:
    for raw in ["/resume", "/models", "/uploads", "/artifacts", "/switch"]:
        command = parse_slash_command(raw)
        assert command.kind is SlashCommandKind.UNKNOWN


def test_help_lists_expected_interactive_commands() -> None:
    help_text = slash_command_help()

    retained = [
        "/help",
        "/new",
        "/threads",
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
        "/run",
        "/quit",
    ]
    deleted = ["/resume", "/models", "/uploads", "/artifacts", "/switch"]
    for command in retained:
        assert command in help_text
    for command in deleted:
        assert command not in help_text


def test_command_suggestions_exclude_deleted_commands() -> None:
    suggestions = {item.name for item in command_suggestions("/")}

    assert suggestions == {
        "new",
        "threads",
        "run",
        "status",
        "model",
        "thinking",
        "skills",
        "tools",
        "mcp",
        "memory",
        "details",
        "usage",
        "config",
        "help",
        "quit",
    }
