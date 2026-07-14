import asyncio

import pytest

from awesome_agent.application.interactions import (
    InteractionBusy,
    InteractionChoice,
    InteractionCoordinator,
    InteractionDecision,
    InteractionKind,
    state_reset_choices,
    tool_approval_choices,
)


@pytest.mark.asyncio
async def test_create_file_interaction_is_structured_and_resolves_once() -> None:
    coordinator = InteractionCoordinator()
    pending = coordinator.create(
        kind=InteractionKind.TOOL_APPROVAL,
        prompt="Do you want to create circle_area.py?",
        operation="create",
        target="circle_area.py",
        capability="workspace.write",
        choices=(
            InteractionChoice(
                decision=InteractionDecision.ALLOW_ONCE,
                label="Yes",
            ),
            InteractionChoice(
                decision=InteractionDecision.ALLOW_THREAD_WRITES,
                label="Yes, allow all edits during this session",
            ),
            InteractionChoice(decision=InteractionDecision.DENY, label="No"),
        ),
    )
    assert pending.operation == "create"
    assert pending.target == "circle_area.py"
    assert [choice.decision for choice in pending.choices] == [
        InteractionDecision.ALLOW_ONCE,
        InteractionDecision.ALLOW_THREAD_WRITES,
        InteractionDecision.DENY,
    ]
    waiter = asyncio.create_task(coordinator.wait(pending.id))
    assert coordinator.resolve(pending.id, InteractionDecision.ALLOW_ONCE) is True
    assert await waiter is InteractionDecision.ALLOW_ONCE
    assert coordinator.pending is None


@pytest.mark.parametrize("capability", ["workspace.delete", "shell.execute"])
def test_delete_and_shell_approval_never_offer_thread_write_grant(
    capability: str,
) -> None:
    assert [choice.decision for choice in tool_approval_choices(capability)] == [
        InteractionDecision.ALLOW_ONCE,
        InteractionDecision.DENY,
    ]


def test_state_reset_choices_are_explicit_and_can_be_validated_without_resolving(
) -> None:
    coordinator = InteractionCoordinator()
    pending = coordinator.create(
        kind=InteractionKind.STATE_RESET,
        prompt="Awesome needs to reset local state",
        operation="reset_local_state",
        target="local state",
        capability=None,
        choices=state_reset_choices(),
    )

    assert [choice.label for choice in pending.choices] == [
        "Reset local state and continue",
        "Exit",
    ]
    assert coordinator.allows(pending.id, InteractionDecision.RESET_STATE) is True
    assert coordinator.allows(pending.id, InteractionDecision.TRUST) is False
    assert coordinator.pending is pending


def test_only_one_interaction_can_be_pending() -> None:
    coordinator = InteractionCoordinator()
    coordinator.create(
        kind=InteractionKind.WORKSPACE_TRUST,
        prompt="Trust workspace?",
        operation="trust",
        target="workspace",
        capability=None,
        choices=(
            InteractionChoice(decision=InteractionDecision.TRUST, label="Yes"),
            InteractionChoice(decision=InteractionDecision.DENY, label="No"),
        ),
    )
    with pytest.raises(InteractionBusy):
        coordinator.create(
            kind=InteractionKind.TOOL_APPROVAL,
            prompt="Allow?",
            operation="run",
            target="pytest",
            capability="shell.execute",
            choices=(
                InteractionChoice(
                    decision=InteractionDecision.ALLOW_ONCE,
                    label="Yes",
                ),
                InteractionChoice(decision=InteractionDecision.DENY, label="No"),
            ),
        )


@pytest.mark.asyncio
async def test_resolve_before_wait_is_not_lost() -> None:
    coordinator = InteractionCoordinator()
    pending = coordinator.create(
        kind=InteractionKind.WORKSPACE_TRUST,
        prompt="Trust workspace?",
        operation="trust",
        target="workspace",
        capability=None,
        choices=(
            InteractionChoice(decision=InteractionDecision.TRUST, label="Yes"),
            InteractionChoice(decision=InteractionDecision.DENY, label="No"),
        ),
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
        operation="trust",
        target="workspace",
        capability=None,
        choices=(
            InteractionChoice(decision=InteractionDecision.TRUST, label="Yes"),
            InteractionChoice(decision=InteractionDecision.DENY, label="No"),
        ),
    )

    assert coordinator.resolve(pending.id, InteractionDecision.ALLOW_ONCE) is False
    coordinator.cancel_pending()

    assert await coordinator.wait(pending.id) is InteractionDecision.DENY
    assert coordinator.pending is None
