from __future__ import annotations

from awesome_agent.surfaces.commands import (
    SlashCommandKind,
    command_suggestions,
    parse_slash_command,
    slash_command_help,
)


def test_help_uses_shared_command_registry() -> None:
    help_text = slash_command_help()

    assert "/new - Start a new local conversation." in help_text
    assert (
        "/status - Show the current thread, run, API, and sandbox status." in help_text
    )


def test_aliases_resolve_through_shared_registry() -> None:
    assert parse_slash_command("/switch").kind is SlashCommandKind.THREADS
    assert parse_slash_command("/model").kind is SlashCommandKind.MODELS


def test_command_suggestions_return_all_commands_for_slash() -> None:
    suggestions = command_suggestions("/")

    assert suggestions
    assert suggestions[0].name == "new"
    assert {definition.name for definition in suggestions} >= {"new", "status", "help"}


def test_command_suggestions_filter_by_prefix() -> None:
    suggestions = command_suggestions("/s")

    names = [definition.name for definition in suggestions]
    assert "status" in names
    assert "skills" in names
    assert "new" not in names
