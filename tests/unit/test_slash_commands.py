from awesome_agent.cli.slash_commands import (
    SlashCommandKind,
    command_suggestions,
    parse_slash_command,
    slash_command_help,
)


def test_run_command_is_removed() -> None:
    command = parse_slash_command("/run build a game")

    assert command.kind is SlashCommandKind.UNKNOWN
    assert command.argument == "run build a game"


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
        "/quit",
    ]
    deleted = ["/resume", "/models", "/uploads", "/artifacts", "/switch", "/run"]
    for command in retained:
        assert command in help_text
    for command in deleted:
        assert command not in help_text


def test_command_suggestions_exclude_deleted_commands() -> None:
    suggestions = {item.name for item in command_suggestions("/")}

    assert suggestions == {
        "new",
        "threads",
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
