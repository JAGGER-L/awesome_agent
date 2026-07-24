from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from awesome_agent.application.command_results import CommandOutcome, error
from awesome_agent.application.commands import (
    COMMAND_OWNERS,
    CommandIntent,
    CommandName,
    CommandOwner,
)
from awesome_agent.application.foreground import ForegroundArbiter, ForegroundBusy

type CommandHandler = Callable[[CommandIntent], Awaitable[CommandOutcome]]

_APPLICATION_COMMANDS = frozenset(
    name for name, owner in COMMAND_OWNERS.items() if owner is not CommandOwner.INK
)

_OBSERVATION_COMMANDS = frozenset(
    {
        CommandName.CONTEXT,
        CommandName.WORKSPACE,
        CommandName.TOOLS,
        CommandName.STATUS,
        CommandName.USAGE,
        CommandName.CONFIG,
    }
)


class InvalidCommandInventory(ValueError):
    pass


class CommandDispatcher:
    """Immutable, complete authority for Core-owned slash commands."""

    def __init__(
        self,
        handlers: Mapping[CommandName, CommandHandler],
        *,
        foreground: ForegroundArbiter | None = None,
        has_pending_interaction: Callable[[], bool] = lambda: False,
    ) -> None:
        names = frozenset(handlers)
        if names != _APPLICATION_COMMANDS:
            missing = sorted(name.value for name in _APPLICATION_COMMANDS - names)
            unexpected = sorted(name.value for name in names - _APPLICATION_COMMANDS)
            raise InvalidCommandInventory(
                "Invalid command inventory; "
                f"missing={missing}, unexpected={unexpected}."
            )
        self._handlers = dict(handlers)
        self._foreground = foreground
        self._has_pending_interaction = has_pending_interaction

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
        foreground = self._foreground
        if foreground is None:
            return await handler(intent)
        observation = _is_observation(intent)
        if foreground.closing or foreground.exclusive_active:
            return _operation_busy()
        if foreground.operation_active:
            return await handler(intent) if observation else _operation_busy()
        if self._has_pending_interaction():
            return (
                await handler(intent)
                if observation
                else error(
                    "interaction_busy",
                    "Resolve the pending interaction before changing state.",
                )
            )
        if observation:
            return await handler(intent)
        try:
            lease = foreground.acquire_exclusive()
        except ForegroundBusy:
            return _operation_busy()
        async with lease:
            if self._has_pending_interaction():
                return error(
                    "interaction_busy",
                    "Resolve the pending interaction before changing state.",
                )
            return await handler(intent)


def _is_observation(intent: CommandIntent) -> bool:
    if intent.name in _OBSERVATION_COMMANDS:
        return True
    if intent.name is not CommandName.MCP:
        return False
    return not intent.arguments or (
        intent.arguments[0] == "status" and len(intent.arguments) <= 2
    )


def _operation_busy() -> CommandOutcome:
    return error(
        "operation_busy",
        "Another foreground operation is active.",
    )
