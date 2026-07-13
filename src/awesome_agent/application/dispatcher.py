from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from awesome_agent.application.command_results import CommandOutcome, error
from awesome_agent.application.commands import (
    COMMAND_OWNERS,
    CommandIntent,
    CommandName,
    CommandOwner,
)

type CommandHandler = Callable[[CommandIntent], Awaitable[CommandOutcome]]

_APPLICATION_COMMANDS = frozenset(
    name for name, owner in COMMAND_OWNERS.items() if owner is not CommandOwner.INK
)


class InvalidCommandInventory(ValueError):
    pass


class CommandDispatcher:
    """Immutable, complete authority for Core-owned slash commands."""

    def __init__(self, handlers: Mapping[CommandName, CommandHandler]) -> None:
        names = frozenset(handlers)
        if names != _APPLICATION_COMMANDS:
            missing = sorted(name.value for name in _APPLICATION_COMMANDS - names)
            unexpected = sorted(name.value for name in names - _APPLICATION_COMMANDS)
            raise InvalidCommandInventory(
                "Invalid command inventory; "
                f"missing={missing}, unexpected={unexpected}."
            )
        self._handlers = dict(handlers)

    @property
    def registered_names(self) -> tuple[CommandName, ...]:
        return tuple(sorted(self._handlers, key=lambda name: name.value))

    async def dispatch(self, intent: CommandIntent) -> CommandOutcome:
        handler = self._handlers.get(intent.name)
        if handler is None:
            return error(
                "command_not_available",
                "Command is not available in the current product phase.",
            )
        return await handler(intent)
