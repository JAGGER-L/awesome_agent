from __future__ import annotations

from awesome_agent.surfaces.commands import (
    SlashCommandKind,
    command_suggestions,
    parse_slash_command,
    slash_command_help,
)


def test_help_uses_shared_command_registry() -> None:
    help_text = slash_command_help()

    assert "/new - Start a new conversation." in help_text
    assert "/attach - Attach a local file to the next turn." in help_text
    assert "/status - Show current state." in help_text


def test_deleted_commands_parse_as_unknown() -> None:
    for raw in ["/resume", "/models", "/uploads", "/artifacts", "/switch"]:
        assert parse_slash_command(raw).kind is SlashCommandKind.UNKNOWN


def test_model_is_the_only_model_command() -> None:
    assert parse_slash_command("/model").kind is SlashCommandKind.MODEL


def test_command_suggestions_return_all_commands_for_slash() -> None:
    suggestions = command_suggestions("/")

    assert suggestions
    assert suggestions[0].name == "new"
    assert {definition.name for definition in suggestions} >= {
        "new",
        "attach",
        "status",
        "help",
        "thinking",
    }
    assert not {
        "resume",
        "models",
        "uploads",
        "artifacts",
        "switch",
    } & {definition.name for definition in suggestions}


def test_command_suggestions_filter_by_prefix() -> None:
    suggestions = command_suggestions("/s")

    names = [definition.name for definition in suggestions]
    assert "status" in names
    assert "skills" in names
    assert "new" not in names
