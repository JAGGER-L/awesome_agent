from awesome_agent.cli.slash_commands import (
    SlashCommandKind,
    parse_slash_command,
    slash_command_help,
)


def test_parse_known_command_with_argument() -> None:
    command = parse_slash_command("/resume project alpha")

    assert command.kind is SlashCommandKind.RESUME
    assert command.argument == "project alpha"


def test_parse_switch_alias_as_threads() -> None:
    command = parse_slash_command("/switch")

    assert command.kind is SlashCommandKind.THREADS


def test_parse_model_alias_as_models() -> None:
    command = parse_slash_command("/model")

    assert command.kind is SlashCommandKind.MODELS


def test_help_lists_expected_interactive_commands() -> None:
    help_text = slash_command_help()

    for command in [
        "/new",
        "/threads",
        "/resume",
        "/model",
        "/skills",
        "/tools",
        "/mcp",
        "/artifacts",
        "/details",
        "/usage",
        "/config",
        "/quit",
    ]:
        assert command in help_text
