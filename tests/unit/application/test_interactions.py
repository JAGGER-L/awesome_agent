import asyncio

import pytest

from awesome_agent.application.interactions import (
    InteractionBusy,
    InteractionChoice,
    InteractionCoordinator,
    InteractionDecision,
    InteractionKind,
    full_access_confirmation_choices,
    recovery_decision_choices,
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
        thread_id="thread_1",
        turn_id="turn_1",
        operation_id="operation_1",
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


def test_network_approval_choices_cover_search_queries_and_fetch_urls() -> None:
    choices = tool_approval_choices("network.read")

    assert [choice.decision for choice in choices] == [
        InteractionDecision.DENY,
        InteractionDecision.ALLOW_ONCE,
        InteractionDecision.ALLOW_THREAD_NETWORK,
    ]
    rendered = " ".join(choice.description or "" for choice in choices)
    assert "search query or URL" in rendered
    assert "Web search and fetch requests" in rendered


def test_state_reset_choices_are_explicit_and_can_be_validated_without_resolving() -> (
    None
):
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


def test_full_access_confirmation_is_thread_bound_and_safe_by_default() -> None:
    coordinator = InteractionCoordinator()
    pending = coordinator.create(
        kind=InteractionKind.FULL_ACCESS_CONFIRMATION,
        prompt="Enable Full access?",
        operation="enable",
        target="full access",
        capability=None,
        choices=full_access_confirmation_choices(),
        thread_id="thread_1",
        permission_generation=3,
    )

    assert pending.thread_id == "thread_1"
    assert pending.permission_generation == 3
    assert pending.choices[0].decision is InteractionDecision.DENY


def test_recovery_decision_is_turn_bound_and_generation_changes() -> None:
    coordinator = InteractionCoordinator()
    first = coordinator.create(
        kind=InteractionKind.RECOVERY_DECISION,
        prompt="Resume unfinished Turn?",
        operation="recover",
        target="unfinished Turn",
        capability=None,
        choices=recovery_decision_choices(uncertain=False),
        thread_id="thread_1",
        turn_id="turn_1",
    )

    assert first.generation == 1
    assert [choice.decision for choice in first.choices] == [
        InteractionDecision.RETRY,
        InteractionDecision.ABORT,
    ]
    assert coordinator.discard(first.id) is True

    second = coordinator.create(
        kind=InteractionKind.RECOVERY_DECISION,
        prompt="A tool outcome is uncertain.",
        operation="recover",
        target="uncertain external tool call",
        capability=None,
        choices=recovery_decision_choices(uncertain=True),
        thread_id="thread_2",
        turn_id="turn_2",
    )

    assert second.generation == 2
    assert second.thread_id == "thread_2"
    assert second.turn_id == "turn_2"
    assert [choice.decision for choice in second.choices] == [
        InteractionDecision.ABORT,
        InteractionDecision.RETRY,
    ]


def test_recovery_decision_requires_thread_and_turn_authority() -> None:
    coordinator = InteractionCoordinator()

    with pytest.raises(ValueError, match="Recovery decision requires Turn authority"):
        coordinator.create(
            kind=InteractionKind.RECOVERY_DECISION,
            prompt="Resume?",
            operation="recover",
            target="unfinished Turn",
            capability=None,
            choices=recovery_decision_choices(uncertain=False),
            thread_id="thread_1",
        )


@pytest.mark.asyncio
async def test_second_response_is_rejected_before_waiter_clears_pending() -> None:
    coordinator = InteractionCoordinator()
    pending = coordinator.create(
        kind=InteractionKind.TOOL_APPROVAL,
        prompt="Approve?",
        operation="write",
        target="file.txt",
        capability="workspace.write",
        choices=tool_approval_choices("workspace.write"),
        thread_id="thread_1",
        turn_id="turn_1",
        operation_id="operation_1",
    )
    waiter = asyncio.create_task(coordinator.wait(pending.id))
    await asyncio.sleep(0)

    assert coordinator.resolve(pending.id, InteractionDecision.ALLOW_ONCE) is True
    assert coordinator.resolve(pending.id, InteractionDecision.DENY) is False
    assert await waiter is InteractionDecision.ALLOW_ONCE
    assert coordinator.pending is None


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
            thread_id="thread_1",
            turn_id="turn_1",
            operation_id="operation_1",
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
