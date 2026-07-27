import asyncio
from collections.abc import Awaitable, Callable

import pytest

from awesome_agent.application.command_results import (
    CommandError,
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
from awesome_agent.application.foreground import ForegroundArbiter, ForegroundKind
from awesome_agent.application.operations import OperationBusy, OperationController
from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.resource_lock import (
    ResourceLockTimeout,
    ResourceLockUnavailable,
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

    assert len(handlers) == 26
    assert set(dispatcher.registered_names) == set(handlers)
    outcome = await dispatcher.dispatch(CommandIntent(name=CommandName.TOOLS))
    assert outcome == result(NoticeCommandPayload(message="tools"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "error_code"),
    [
        (ResourceLockTimeout(), "operation_busy"),
        (ResourceLockUnavailable(), "state_unavailable"),
    ],
)
async def test_dispatcher_normalizes_user_state_lock_failures(
    failure: Exception,
    error_code: str,
) -> None:
    async def fail(_: CommandIntent) -> CommandOutcome:
        raise failure

    handlers = _complete_handlers()
    handlers[CommandName.MEMORY] = fail
    dispatcher = CommandDispatcher(handlers)

    outcome = await dispatcher.dispatch(
        CommandIntent(name=CommandName.MEMORY, arguments=("local", "on"))
    )

    assert isinstance(outcome, CommandError)
    assert outcome.code == error_code
    assert "lock" not in outcome.message.lower()


def test_dispatcher_rejects_incomplete_or_surface_owned_inventory() -> None:
    with pytest.raises(InvalidCommandInventory):
        CommandDispatcher({})
    with pytest.raises(InvalidCommandInventory):
        CommandDispatcher(
            {**_complete_handlers(), CommandName.HELP: successful_handler}
        )


@pytest.mark.asyncio
async def test_dispatcher_allows_only_exact_observations_during_operation() -> None:
    foreground = ForegroundArbiter()
    dispatcher = CommandDispatcher(
        _complete_handlers(),
        foreground=foreground,
    )
    operation = foreground.acquire_operation()

    for intent in (
        CommandIntent(name=CommandName.CONTEXT),
        CommandIntent(name=CommandName.WORKSPACE),
        CommandIntent(name=CommandName.TOOLS),
        CommandIntent(name=CommandName.MCP),
        CommandIntent(
            name=CommandName.MCP,
            arguments=(
                "status",
                "server",
            ),
        ),
        CommandIntent(name=CommandName.STATUS),
        CommandIntent(name=CommandName.USAGE),
        CommandIntent(name=CommandName.CONFIG),
        CommandIntent(name=CommandName.SEARCH, arguments=("query",)),
    ):
        outcome = await dispatcher.dispatch(intent)
        assert not isinstance(outcome, CommandError)
        assert outcome.kind == "result"

    for intent in (
        CommandIntent(name=CommandName.DIFF),
        CommandIntent(name=CommandName.DOCTOR),
        CommandIntent(name=CommandName.MCP, arguments=("restart", "server")),
        CommandIntent(name=CommandName.NEW),
        CommandIntent(name=CommandName.FORK),
        CommandIntent(name=CommandName.RETRY),
        CommandIntent(
            name=CommandName.SEARCH,
            arguments=("query", "thread_1"),
        ),
    ):
        outcome = await dispatcher.dispatch(intent)
        assert isinstance(outcome, CommandError)
        assert outcome.code == "operation_busy"

    operation.release()


@pytest.mark.asyncio
async def test_search_selection_is_observation_but_continuation_is_mutation() -> None:
    foreground = ForegroundArbiter()
    dispatcher = CommandDispatcher(_complete_handlers(), foreground=foreground)
    operation = foreground.acquire_operation()

    selection = await dispatcher.dispatch(
        CommandIntent(name=CommandName.SEARCH, arguments=("quoted query",))
    )
    continuation = await dispatcher.dispatch(
        CommandIntent(
            name=CommandName.SEARCH,
            arguments=("quoted query", "thread_1"),
        )
    )

    assert not isinstance(selection, CommandError)
    assert isinstance(continuation, CommandError)
    assert continuation.code == "operation_busy"
    operation.release()


@pytest.mark.asyncio
async def test_pending_interaction_blocks_mutation_but_not_observation() -> None:
    foreground = ForegroundArbiter()
    pending = True
    dispatcher = CommandDispatcher(
        _complete_handlers(),
        foreground=foreground,
        has_pending_interaction=lambda: pending,
    )

    observation = await dispatcher.dispatch(CommandIntent(name=CommandName.STATUS))
    assert not isinstance(observation, CommandError)
    assert observation.kind == "result"
    blocked = await dispatcher.dispatch(CommandIntent(name=CommandName.RESUME))
    assert isinstance(blocked, CommandError)
    assert blocked.code == "interaction_busy"

    pending = False
    resumed = await dispatcher.dispatch(CommandIntent(name=CommandName.RESUME))
    assert not isinstance(resumed, CommandError)
    assert resumed.kind == "result"


@pytest.mark.asyncio
async def test_retry_handler_starts_an_operation_without_an_exclusive_lease() -> None:
    foreground = ForegroundArbiter()
    emitter = EventEmitter(
        session_id="session_1",
        workspace_key="workspace_1",
        sink=CollectingEventSink(),
    )
    operations = OperationController(emitter, foreground)
    observed_kind: ForegroundKind | None = None

    async def retry_handler(_: CommandIntent) -> CommandOutcome:
        nonlocal observed_kind
        reservation = operations.reserve()
        observed_kind = foreground.active_kind
        operations.abort(reservation)
        return result(NoticeCommandPayload(message="retry"))

    handlers = _complete_handlers()
    handlers[CommandName.RETRY] = retry_handler
    dispatcher = CommandDispatcher(handlers, foreground=foreground)

    outcome = await dispatcher.dispatch(CommandIntent(name=CommandName.RETRY))

    assert not isinstance(outcome, CommandError)
    assert observed_kind is ForegroundKind.OPERATION
    assert foreground.active_kind is None


@pytest.mark.asyncio
async def test_retry_rechecks_pending_interaction_after_mutation_guard() -> None:
    foreground = ForegroundArbiter()
    pending = False
    handled = False

    async def mutation_guard() -> None:
        nonlocal pending
        pending = True

    async def retry_handler(_: CommandIntent) -> CommandOutcome:
        nonlocal handled
        handled = True
        return result(NoticeCommandPayload(message="retry"))

    handlers = _complete_handlers()
    handlers[CommandName.RETRY] = retry_handler
    dispatcher = CommandDispatcher(
        handlers,
        foreground=foreground,
        has_pending_interaction=lambda: pending,
        mutation_guard=mutation_guard,
    )

    outcome = await dispatcher.dispatch(CommandIntent(name=CommandName.RETRY))

    assert isinstance(outcome, CommandError)
    assert outcome.code == "interaction_busy"
    assert handled is False


@pytest.mark.asyncio
async def test_retry_rechecks_foreground_after_mutation_guard() -> None:
    foreground = ForegroundArbiter()
    operation = None
    handled = False

    async def mutation_guard() -> None:
        nonlocal operation
        operation = foreground.acquire_operation()

    async def retry_handler(_: CommandIntent) -> CommandOutcome:
        nonlocal handled
        handled = True
        return result(NoticeCommandPayload(message="retry"))

    handlers = _complete_handlers()
    handlers[CommandName.RETRY] = retry_handler
    dispatcher = CommandDispatcher(
        handlers,
        foreground=foreground,
        mutation_guard=mutation_guard,
    )

    outcome = await dispatcher.dispatch(CommandIntent(name=CommandName.RETRY))

    assert isinstance(outcome, CommandError)
    assert outcome.code == "operation_busy"
    assert handled is False
    assert operation is not None
    operation.release()


@pytest.mark.asyncio
async def test_recovery_guard_blocks_mutations_but_not_observations() -> None:
    foreground = ForegroundArbiter()
    handled: list[CommandName] = []

    async def handler(intent: CommandIntent) -> CommandOutcome:
        handled.append(intent.name)
        return result(NoticeCommandPayload(message=intent.name.value))

    async def require_consistent_state() -> None:
        raise RuntimeError("recovery required")

    dispatcher = CommandDispatcher(
        {name: handler for name in _complete_handlers()},
        foreground=foreground,
        mutation_guard=require_consistent_state,
    )

    observed = await dispatcher.dispatch(CommandIntent(name=CommandName.STATUS))
    assert not isinstance(observed, CommandError)
    with pytest.raises(RuntimeError, match="recovery required"):
        await dispatcher.dispatch(CommandIntent(name=CommandName.NEW))
    assert handled == [CommandName.STATUS]


@pytest.mark.asyncio
async def test_mutation_operation_and_shutdown_boundaries_are_atomic() -> None:
    foreground = ForegroundArbiter()
    emitter = EventEmitter(
        session_id="session_1",
        workspace_key="workspace_1",
        sink=CollectingEventSink(),
    )
    operations = OperationController(emitter, foreground)
    entered = asyncio.Barrier(2)
    release = asyncio.Event()
    calls = 0

    async def blocked_handler(intent: CommandIntent) -> CommandOutcome:
        nonlocal calls
        calls += 1
        await entered.wait()
        await release.wait()
        return result(NoticeCommandPayload(message=intent.name.value))

    handlers = _complete_handlers()
    handlers[CommandName.NEW] = blocked_handler
    dispatcher = CommandDispatcher(handlers, foreground=foreground)

    command = asyncio.create_task(
        dispatcher.dispatch(CommandIntent(name=CommandName.NEW))
    )
    await entered.wait()
    with pytest.raises(OperationBusy):
        operations.reserve()
    competing_command = await dispatcher.dispatch(CommandIntent(name=CommandName.NEW))
    assert isinstance(competing_command, CommandError)
    assert competing_command.code == "operation_busy"
    assert calls == 1
    release.set()
    assert not isinstance(await command, CommandError)

    reservation = operations.reserve()
    blocked_by_operation = await dispatcher.dispatch(
        CommandIntent(name=CommandName.NEW)
    )
    assert isinstance(blocked_by_operation, CommandError)
    assert blocked_by_operation.code == "operation_busy"
    operations.abort(reservation)

    foreground.begin_closing()
    blocked_by_shutdown = await dispatcher.dispatch(CommandIntent(name=CommandName.NEW))
    assert isinstance(blocked_by_shutdown, CommandError)
    assert blocked_by_shutdown.code == "operation_busy"
    with pytest.raises(OperationBusy):
        operations.reserve()
