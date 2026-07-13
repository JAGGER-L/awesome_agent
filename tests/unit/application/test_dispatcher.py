from collections.abc import Awaitable, Callable

import pytest

from awesome_agent.application.command_results import (
    CommandOutcome,
    NoticeCommandPayload,
    result,
)
from awesome_agent.application.commands import (
    COMMAND_OWNERS,
    CommandIntent,
    CommandName,
    CommandOwner,
)
from awesome_agent.application.dispatcher import (
    CommandDispatcher,
    InvalidCommandInventory,
)


async def successful_handler(intent: CommandIntent) -> CommandOutcome:
    return result(NoticeCommandPayload(message=intent.name.value))


def _complete_handlers() -> dict[
    CommandName, Callable[[CommandIntent], Awaitable[CommandOutcome]]
]:
    return {
        name: successful_handler
        for name, owner in COMMAND_OWNERS.items()
        if owner is not CommandOwner.INK
    }


@pytest.mark.asyncio
async def test_dispatcher_requires_and_exposes_exact_core_inventory() -> None:
    handlers = _complete_handlers()
    dispatcher = CommandDispatcher(handlers)

    assert len(handlers) == 20
    assert set(dispatcher.registered_names) == set(handlers)
    outcome = await dispatcher.dispatch(CommandIntent(name=CommandName.TOOLS))
    assert outcome == result(NoticeCommandPayload(message="tools"))


def test_dispatcher_rejects_incomplete_or_surface_owned_inventory() -> None:
    with pytest.raises(InvalidCommandInventory):
        CommandDispatcher({})
    with pytest.raises(InvalidCommandInventory):
        CommandDispatcher(
            {**_complete_handlers(), CommandName.HELP: successful_handler}
        )
