from __future__ import annotations

from collections.abc import Awaitable, Callable

from awesome_agent.application.commands import (
    COMMAND_OWNERS,
    CommandIntent,
    CommandName,
    CommandOwner,
    CommandResult,
    CommandStatus,
)

type CommandHandler = Callable[[CommandIntent], Awaitable[CommandResult]]


class DuplicateCommandHandler(ValueError):
    pass


class InvalidCommandOwner(ValueError):
    pass


class CommandDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[CommandName, CommandHandler] = {}

    @property
    def registered_names(self) -> tuple[CommandName, ...]:
        return tuple(sorted(self._handlers, key=lambda name: name.value))

    def register(self, name: CommandName, handler: CommandHandler) -> None:
        if COMMAND_OWNERS[name] is not CommandOwner.APPLICATION:
            raise InvalidCommandOwner(name.value)
        if name in self._handlers:
            raise DuplicateCommandHandler(name.value)
        self._handlers[name] = handler

    async def dispatch(self, intent: CommandIntent) -> CommandResult:
        handler = self._handlers.get(intent.name)
        if handler is None:
            return CommandResult(
                status=CommandStatus.ERROR,
                content="Command is not available in the current product phase.",
                data={"error_code": "command_not_available"},
            )
        return await handler(intent)
