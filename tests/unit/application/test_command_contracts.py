import pytest
from pydantic import ValidationError

from awesome_agent.application.commands import (
    COMMAND_OWNERS,
    CommandIntent,
    CommandName,
    CommandOption,
    CommandOwner,
    CommandResult,
    CommandSelection,
    CommandStatus,
)
from awesome_agent.application.contracts import (
    ApplicationState,
    OperationAccepted,
    ProductError,
    ProductErrorCode,
    ThreadListResult,
    ThreadReadResult,
)
from awesome_agent.config import SecretStatus


def test_command_ownership_matrix_is_complete() -> None:
    application = {
        "new",
        "resume",
        "context",
        "compact",
        "model",
        "thinking",
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
        "usage",
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


@pytest.mark.parametrize(
    "removed",
    [
        "mode",
        "history",
        "threads",
        "attach",
        "clear",
        "permissions",
        "sandbox",
        "api",
        "agent",
        "team",
    ],
)
def test_removed_commands_have_no_alias(removed: str) -> None:
    with pytest.raises(ValueError):
        CommandName(removed)


def test_command_selection_is_stateless_and_round_trips() -> None:
    selection = CommandSelection(
        prompt="Thinking mode",
        options=(
            CommandOption(value="off", label="Off", selected=True),
            CommandOption(
                value="on",
                label="On",
                description="Show live reasoning deltas.",
            ),
        ),
    )
    result = CommandResult(
        status=CommandStatus.SUCCESS,
        selection=selection,
    )

    restored = CommandResult.model_validate_json(result.model_dump_json())

    assert restored.selection == selection
    assert not hasattr(restored, "interaction_id")


@pytest.mark.parametrize(
    "options",
    [
        (),
        (
            CommandOption(value="same", label="First"),
            CommandOption(value="same", label="Second"),
        ),
        (
            CommandOption(value="on", label="On", selected=True),
            CommandOption(value="off", label="Off", selected=True),
        ),
    ],
)
def test_command_selection_requires_unique_options_and_one_selection(
    options: tuple[CommandOption, ...],
) -> None:
    with pytest.raises(ValidationError):
        CommandSelection(prompt="Choose", options=options)


def test_surface_neutral_application_contracts_hide_secret_values() -> None:
    state = ApplicationState(
        initialized=True,
        session_id="session_1",
        workspace_key="workspace_1",
        workspace_trusted=True,
        current_thread_id=None,
        current_model=None,
        thinking_enabled=False,
        skill_mode="auto",
        active_operation_id=None,
        pending_interaction_id=None,
        configuration_valid=True,
        secret_status=SecretStatus(deepseek_api_key=True),
    )
    accepted = OperationAccepted(
        operation_id="operation_1",
        thread_id="thread_1",
        turn_id="turn_1",
    )
    error = ProductError(
        code=ProductErrorCode.OPERATION_BUSY,
        message="Another operation is active.",
        retryable=True,
    )

    assert state.model_dump(mode="json")["secret_status"] == {
        "deepseek_api_key": True,
        "moonshot_api_key": False,
        "mem0_api_key": False,
    }
    with pytest.raises(ValidationError):
        SecretStatus.model_validate({"deepseek_api_key": "raw-secret-value"})
    assert accepted.turn_id == "turn_1"
    assert error.code is ProductErrorCode.OPERATION_BUSY


def test_thread_results_use_conversation_contracts() -> None:
    listed = ThreadListResult(threads=())

    assert listed.threads == ()
    with pytest.raises(ValidationError):
        ThreadReadResult.model_validate({"view": {}})
