from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MailboxMessage(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    team_id: UUID
    sender_id: UUID
    recipient_id: UUID
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TeamMailbox:
    def __init__(self, *, team_id: UUID, leader_id: UUID) -> None:
        self.team_id = team_id
        self.leader_id = leader_id
        self._member_ids: set[UUID] = {leader_id}
        self._messages: list[MailboxMessage] = []

    def add_member(self, agent_id: UUID) -> None:
        self._member_ids.add(agent_id)

    def remove_member(self, agent_id: UUID) -> None:
        self._member_ids.discard(agent_id)

    def send(
        self, *, sender_id: UUID, recipient_id: UUID, content: str
    ) -> MailboxMessage:
        if sender_id not in self._member_ids or recipient_id not in self._member_ids:
            raise ValueError("Mailbox sender and recipient must be team members.")
        message = MailboxMessage(
            team_id=self.team_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            content=content,
        )
        self._messages.append(message)
        return message

    def list_for(self, agent_id: UUID) -> list[MailboxMessage]:
        if agent_id == self.leader_id:
            return list(self._messages)
        return [
            message
            for message in self._messages
            if message.sender_id == agent_id or message.recipient_id == agent_id
        ]
