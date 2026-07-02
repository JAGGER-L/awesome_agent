from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConversationStreamEventKind(StrEnum):
    TURN_STARTED = "turn.started"
    MESSAGE_CREATED = "message.created"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    REASONING_STARTED = "reasoning.started"
    REASONING_DELTA = "reasoning.delta"
    REASONING_COMPLETED = "reasoning.completed"
    USAGE_UPDATED = "usage.updated"
    TURN_COMPLETED = "turn.completed"
    ERROR = "error"


class ConversationStreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: ConversationStreamEventKind
    thread_id: UUID
    turn_id: UUID
    sequence: int = Field(ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trace_id: str
    payload: dict[str, object] = Field(default_factory=dict)


class UnknownConversationStreamEvent(ValueError):
    pass


def parse_conversation_stream_event(
    payload: dict[str, object],
) -> ConversationStreamEvent:
    try:
        return ConversationStreamEvent.model_validate(payload)
    except ValidationError as error:
        event_name = payload.get("event")
        if event_name not in set(ConversationStreamEventKind):
            raise UnknownConversationStreamEvent(
                f"Unknown conversation stream event: {event_name}"
            ) from error
        raise
