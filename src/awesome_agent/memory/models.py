from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MemoryKind(StrEnum):
    USER = "user"
    OPERATIONAL = "operational"


class MemorySource(StrEnum):
    AGENT_EXPERIENCE = "agent_experience"
    USER_STATEMENT = "user_statement"
    MEMORY_RETRIEVAL = "memory_retrieval"


class MemoryCandidate(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    kind: MemoryKind
    content: str
    source: MemorySource
    source_event_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryRecord(BaseModel):
    id: str
    content: str
    metadata: dict[str, str] = Field(default_factory=dict)


class ContextItem(BaseModel):
    event_id: UUID
    content: str


class ContextSummary(BaseModel):
    text: str
    source_event_ids: list[UUID]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
