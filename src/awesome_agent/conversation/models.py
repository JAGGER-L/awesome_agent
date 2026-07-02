from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ThreadMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ThreadMessageKind(StrEnum):
    MESSAGE = "message"
    RUN = "run"
    TOOL = "tool"
    MODEL = "model"
    APPROVAL = "approval"
    ARTIFACT = "artifact"
    ERROR = "error"


class ThreadMessage(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    role: ThreadMessageRole
    content: str
    kind: ThreadMessageKind = ThreadMessageKind.MESSAGE
    run_id: UUID | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    sequence: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationTurn(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID | None = None
    status: str = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
