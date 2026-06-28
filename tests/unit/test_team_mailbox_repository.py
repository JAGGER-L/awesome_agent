from uuid import uuid4

import pytest

from awesome_agent.persistence.team import InMemoryTeamRepository
from awesome_agent.runtime.team_mailbox import (
    MailboxMessage,
    MailboxMessageStatus,
    MailboxMessageType,
    MailboxRoute,
)


@pytest.mark.asyncio
async def test_mailbox_read_and_response_lifecycle() -> None:
    repository = InMemoryTeamRepository()
    message = MailboxMessage(
        team_root_run_id=uuid4(),
        sender_run_id=uuid4(),
        recipient_run_id=uuid4(),
        route=MailboxRoute.LEADER_TO_TEAMMATE,
        message_type=MailboxMessageType.QUESTION,
        subject="Question",
        body_summary="Need status.",
        requires_response=True,
    )
    await repository.create_mailbox_message(message)

    read = await repository.mark_mailbox_read(message.id)
    responded, response = await repository.respond_to_mailbox_message(
        message.id,
        MailboxMessage(
            team_root_run_id=message.team_root_run_id,
            sender_run_id=message.recipient_run_id,
            recipient_run_id=message.sender_run_id,
            route=MailboxRoute.TEAMMATE_TO_LEADER,
            message_type=MailboxMessageType.STATUS,
            subject="Status",
            body_summary="Done.",
        ),
    )

    assert read.status is MailboxMessageStatus.READ
    assert responded.status is MailboxMessageStatus.RESPONDED
    assert response.response_to_message_id == message.id
