from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class MemoryScope(StrEnum):
    USER = "user"
    WORKSPACE = "workspace"


class MemoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^memory_[a-f0-9]{32}$")
    content: str = Field(min_length=1, max_length=2_000)


class MemoryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MemoryScope
    path: Path
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    markdown: str = Field(max_length=1_000_000)
    entries: tuple[MemoryEntry, ...] = ()


class MemoryMutationStatus(StrEnum):
    ADDED = "added"
    REPLACED = "replaced"
    REMOVED = "removed"
    CONFLICT = "memory_conflict"
    NOT_FOUND = "memory_not_found"
    REJECTED = "memory_rejected"
    DISABLED = "memory_disabled"


class MemoryMutationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: MemoryMutationStatus
    scope: MemoryScope
    entry_id: str | None = None
    content_hash: str
    document: MemoryDocument | None = None
    error_code: str | None = None
