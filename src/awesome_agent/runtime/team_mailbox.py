from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from awesome_agent.domain.models import utc_now


class MailboxRouteError(ValueError):
    pass


class MailboxRoute(StrEnum):
    LEADER_TO_TEAMMATE = "leader_to_teammate"
    LEADER_TO_VERIFIER = "leader_to_verifier"
    TEAMMATE_TO_LEADER = "teammate_to_leader"
    TEAMMATE_TO_TEAMMATE = "teammate_to_teammate"
    TEAMMATE_TO_SUBAGENT = "teammate_to_subagent"
    SUBAGENT_TO_TEAMMATE = "subagent_to_teammate"
    VERIFIER_TO_LEADER = "verifier_to_leader"
    VERIFIER_TO_TEAMMATE = "verifier_to_teammate"


class MailboxMessageType(StrEnum):
    ASSIGNMENT = "assignment"
    QUESTION = "question"
    RESULT = "result"
    VERIFICATION = "verification"
    REWORK = "rework"
    STATUS = "status"


class MailboxMessageStatus(StrEnum):
    UNREAD = "unread"
    READ = "read"
    RESPONDED = "responded"
    ARCHIVED = "archived"


class MailboxMessage(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    team_root_run_id: UUID
    sender_run_id: UUID
    sender_agent_id: UUID | None = None
    recipient_run_id: UUID
    recipient_agent_id: UUID | None = None
    route: MailboxRoute
    message_type: MailboxMessageType
    status: MailboxMessageStatus = MailboxMessageStatus.UNREAD
    subject: str = Field(max_length=512)
    body_summary: str
    artifact_refs: list[UUID] = Field(default_factory=list)
    requires_response: bool = False
    response_to_message_id: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)
    read_at: datetime | None = None
    responded_at: datetime | None = None


def validate_mailbox_route(route: MailboxRoute) -> bool:
    if route is MailboxRoute.VERIFIER_TO_TEAMMATE:
        raise MailboxRouteError("verifier can only message leader")
    return True
