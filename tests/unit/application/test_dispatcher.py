import pytest

from awesome_agent.application.commands import (
    CommandIntent,
    CommandName,
    CommandResult,
    CommandStatus,
)
from awesome_agent.application.dispatcher import (
    CommandDispatcher,
    DuplicateCommandHandler,
    InvalidCommandOwner,
)

PHASE_ONE_COMMANDS = {
    CommandName.WORKSPACE,
    CommandName.TOOLS,
    CommandName.DIFF,
    CommandName.UNDO,
    CommandName.REDO,
    CommandName.STATUS,
    CommandName.DOCTOR,
}


async def successful_handler(intent: CommandIntent) -> CommandResult:
    return CommandResult(status=CommandStatus.SUCCESS, content=intent.name.value)


@pytest.mark.asyncio
async def test_dispatcher_has_exact_phase_one_handlers() -> None:
    dispatcher = CommandDispatcher()
    for name in PHASE_ONE_COMMANDS:
        dispatcher.register(name, successful_handler)

    assert set(dispatcher.registered_names) == PHASE_ONE_COMMANDS
    result = await dispatcher.dispatch(CommandIntent(name=CommandName.TOOLS))
    assert result.status is CommandStatus.SUCCESS


def test_dispatcher_rejects_duplicate_and_non_application_handlers() -> None:
    dispatcher = CommandDispatcher()
    dispatcher.register(CommandName.TOOLS, successful_handler)

    with pytest.raises(DuplicateCommandHandler):
        dispatcher.register(CommandName.TOOLS, successful_handler)
    with pytest.raises(InvalidCommandOwner):
        dispatcher.register(CommandName.INIT, successful_handler)
    with pytest.raises(InvalidCommandOwner):
        dispatcher.register(CommandName.HELP, successful_handler)


@pytest.mark.asyncio
async def test_unregistered_accepted_command_is_not_available() -> None:
    dispatcher = CommandDispatcher()

    result = await dispatcher.dispatch(CommandIntent(name=CommandName.NEW))

    assert result.status is CommandStatus.ERROR
    assert result.data["error_code"] == "command_not_available"
