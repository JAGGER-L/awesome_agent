from uuid import uuid4

import pytest

from awesome_agent.runtime.team_mailbox import (
    MailboxMessage,
    MailboxMessageStatus,
    MailboxMessageType,
    MailboxRoute,
    MailboxRouteError,
    validate_mailbox_message,
    validate_mailbox_route,
)


def test_verifier_can_message_leader_but_not_teammate() -> None:
    assert validate_mailbox_route(MailboxRoute.VERIFIER_TO_LEADER)

    with pytest.raises(MailboxRouteError):
        validate_mailbox_route(MailboxRoute.VERIFIER_TO_TEAMMATE)


def test_mailbox_message_defaults_to_unread() -> None:
    message = MailboxMessage(
        team_root_run_id=uuid4(),
        sender_run_id=uuid4(),
        recipient_run_id=uuid4(),
        route=MailboxRoute.LEADER_TO_TEAMMATE,
        message_type=MailboxMessageType.ASSIGNMENT,
        subject="Inspect repository",
        body_summary="Read README and report findings.",
        requires_response=True,
    )

    assert message.status is MailboxMessageStatus.UNREAD


def test_mailbox_message_validates_route() -> None:
    message = MailboxMessage(
        team_root_run_id=uuid4(),
        sender_run_id=uuid4(),
        recipient_run_id=uuid4(),
        route=MailboxRoute.VERIFIER_TO_TEAMMATE,
        message_type=MailboxMessageType.QUESTION,
        subject="Invalid",
        body_summary="Verifier cannot message teammate directly.",
    )

    with pytest.raises(MailboxRouteError):
        validate_mailbox_message(message)
