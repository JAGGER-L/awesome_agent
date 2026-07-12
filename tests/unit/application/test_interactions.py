import asyncio

import pytest

from awesome_agent.application.interactions import (
    InteractionBusy,
    InteractionCoordinator,
    InteractionDecision,
    InteractionKind,
)


@pytest.mark.asyncio
async def test_allow_once_resolves_only_matching_pending_interaction() -> None:
    coordinator = InteractionCoordinator()
    pending = coordinator.create(
        kind=InteractionKind.EXECUTE_BOUNDARY,
        prompt="Allow this command once?",
        choices=(InteractionDecision.ALLOW_ONCE, InteractionDecision.DENY),
        scope="scope_1",
    )
    waiter = asyncio.create_task(coordinator.wait(pending.id))
    assert coordinator.resolve(pending.id, InteractionDecision.ALLOW_ONCE) is True
    assert await waiter is InteractionDecision.ALLOW_ONCE
    assert coordinator.pending is None


def test_only_one_interaction_can_be_pending() -> None:
    coordinator = InteractionCoordinator()
    coordinator.create(
        kind=InteractionKind.WORKSPACE_TRUST,
        prompt="Trust workspace?",
        choices=(InteractionDecision.TRUST, InteractionDecision.DENY),
        scope=None,
    )
    with pytest.raises(InteractionBusy):
        coordinator.create(
            kind=InteractionKind.EXECUTE_BOUNDARY,
            prompt="Allow?",
            choices=(InteractionDecision.ALLOW_ONCE, InteractionDecision.DENY),
            scope="scope_2",
        )


@pytest.mark.asyncio
async def test_resolve_before_wait_is_not_lost() -> None:
    coordinator = InteractionCoordinator()
    pending = coordinator.create(
        kind=InteractionKind.WORKSPACE_TRUST,
        prompt="Trust workspace?",
        choices=(InteractionDecision.TRUST, InteractionDecision.DENY),
        scope=None,
    )

    assert coordinator.resolve(pending.id, InteractionDecision.TRUST) is True

    assert await coordinator.wait(pending.id) is InteractionDecision.TRUST
    assert coordinator.pending is None


@pytest.mark.asyncio
async def test_invalid_choice_preserves_pending_and_cancel_denies() -> None:
    coordinator = InteractionCoordinator()
    pending = coordinator.create(
        kind=InteractionKind.WORKSPACE_TRUST,
        prompt="Trust workspace?",
        choices=(InteractionDecision.TRUST, InteractionDecision.DENY),
        scope=None,
    )

    assert coordinator.resolve(pending.id, InteractionDecision.ALLOW_ONCE) is False
    coordinator.cancel_pending()

    assert await coordinator.wait(pending.id) is InteractionDecision.DENY
    assert coordinator.pending is None
