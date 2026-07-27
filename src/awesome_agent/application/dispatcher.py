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
from awesome_agent.core.resource_lock import (
    ResourceLockTimeout,
    ResourceLockUnavailable,
)

type CommandHandler = Callable[[CommandIntent], Awaitable[CommandOutcome]]
type MutationGuard = Callable[[], Awaitable[None]]

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

_OPERATION_START_COMMANDS = frozenset({CommandName.RETRY})


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
        mutation_guard: MutationGuard | None = None,
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
        self._mutation_guard = mutation_guard

    @property
    def registered_names(self) -> tuple[CommandName, ...]:
        return tuple(sorted(self._handlers, key=lambda name: name.value))

    async def dispatch(self, intent: CommandIntent) -> CommandOutcome:
        try:
            return await self._dispatch(intent)
        except ResourceLockTimeout:
            return error(
                "operation_busy",
                "User state is being changed by another Awesome process.",
            )
        except ResourceLockUnavailable:
            return error(
                "state_unavailable",
                "User state cannot be accessed safely.",
            )

    async def _dispatch(self, intent: CommandIntent) -> CommandOutcome:
        handler = self._handlers.get(intent.name)
        if handler is None:
            return error(
                "command_not_available",
                "Command is not available in the current product phase.",
            )
        observation = _is_observation(intent)
        foreground = self._foreground
        if foreground is None:
            if not observation and self._mutation_guard is not None:
                await self._mutation_guard()
            return await handler(intent)
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
        if intent.name in _OPERATION_START_COMMANDS:
            if self._mutation_guard is not None:
                await self._mutation_guard()
            if (
                foreground.closing
                or foreground.exclusive_active
                or foreground.operation_active
            ):
                return _operation_busy()
            if self._has_pending_interaction():
                return error(
                    "interaction_busy",
                    "Resolve the pending interaction before changing state.",
                )
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
            if self._mutation_guard is not None:
                await self._mutation_guard()
            return await handler(intent)


def _is_observation(intent: CommandIntent) -> bool:
    if intent.name in _OBSERVATION_COMMANDS:
        return True
    if intent.name is CommandName.SEARCH:
        return len(intent.arguments) == 1
    if intent.name is CommandName.MCP:
        return not intent.arguments or (
            intent.arguments[0] == "status" and len(intent.arguments) <= 2
        )
    if intent.name is CommandName.WEB:
        return not intent.arguments or intent.arguments == ("status",)
    return False


def _operation_busy() -> CommandOutcome:
    return error(
        "operation_busy",
        "Another foreground operation is active.",
    )
