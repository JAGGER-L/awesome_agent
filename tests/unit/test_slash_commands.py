import pytest

from awesome_agent.cli.slash_commands import (
    SlashCommandKind,
    parse_slash_command,
    slash_command_help,
)


@pytest.mark.parametrize(
    ("raw", "kind", "argument"),
    [
        ("/new", SlashCommandKind.NEW, ""),
        ("/new build snake game", SlashCommandKind.NEW, "build snake game"),
        ("/status", SlashCommandKind.STATUS, ""),
        ("/models", SlashCommandKind.MODELS, ""),
        ("/memory", SlashCommandKind.MEMORY, ""),
        ("/help", SlashCommandKind.HELP, ""),
    ],
)
def test_parse_known_slash_commands(
    raw: str,
    kind: SlashCommandKind,
    argument: str,
) -> None:
    parsed = parse_slash_command(raw)

    assert parsed.kind is kind
    assert parsed.argument == argument


def test_non_slash_input_is_user_message() -> None:
    parsed = parse_slash_command("please inspect this repo")

    assert parsed.kind is SlashCommandKind.USER_MESSAGE
    assert parsed.argument == "please inspect this repo"


def test_unknown_slash_command_is_error() -> None:
    parsed = parse_slash_command("/dance")

    assert parsed.kind is SlashCommandKind.UNKNOWN
    assert parsed.argument == "dance"


def test_help_lists_required_commands() -> None:
    text = slash_command_help()

    for command in ["/new", "/status", "/models", "/memory", "/help"]:
        assert command in text
