from awesome_agent.application.commands import (
    COMMAND_OWNERS,
    CommandIntent,
    CommandName,
    CommandOwner,
)


def test_command_ownership_matrix_is_complete() -> None:
    application = {
        "new",
        "resume",
        "history",
        "context",
        "compact",
        "model",
        "mode",
        "workspace",
        "diff",
        "undo",
        "redo",
        "tools",
        "skills",
        "skill",
        "mcp",
        "memory",
        "status",
        "doctor",
        "config",
    }
    skill = {"init", "review", "debug", "test", "commit"}
    ink = {"help", "theme", "details", "copy", "editor", "quit"}

    assert {
        name.value
        for name, owner in COMMAND_OWNERS.items()
        if owner is CommandOwner.APPLICATION
    } == application
    assert {
        name.value
        for name, owner in COMMAND_OWNERS.items()
        if owner is CommandOwner.SKILL
    } == skill
    assert {
        name.value
        for name, owner in COMMAND_OWNERS.items()
        if owner is CommandOwner.INK
    } == ink
    assert set(COMMAND_OWNERS) == set(CommandName)


def test_command_intent_round_trips() -> None:
    intent = CommandIntent(name=CommandName.UNDO, arguments=("change_1",))
    assert CommandIntent.model_validate_json(intent.model_dump_json()) == intent
