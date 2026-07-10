from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class MemoryPolicyStatus(StrEnum):
    ELIGIBLE = "eligible"
    REJECTED = "rejected"


class MemoryPolicyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: MemoryPolicyStatus
    content: str | None = Field(default=None, max_length=2_000)
    error_code: str | None = Field(default=None, max_length=128)
    message: str | None = Field(default=None, max_length=500)


class LocalMemoryScopeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MemoryScope
    label: str = Field(min_length=1, max_length=300)
    exists: bool
    entry_count: int = Field(ge=0)
    error_code: str | None = Field(default=None, max_length=128)


class LocalMemoryStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    scopes: tuple[LocalMemoryScopeStatus, ...] = ()


class CloudMemory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=500)
    scope: MemoryScope
    fact_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    workspace_key: str | None = Field(
        default=None,
        pattern=r"^ws_[a-f0-9]{32}$",
    )

    @model_validator(mode="after")
    def validate_scope(self) -> CloudMemory:
        if (self.scope is MemoryScope.WORKSPACE) != (self.workspace_key is not None):
            raise ValueError("workspace scope requires only an opaque workspace key")
        return self


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MemoryScope
    content: str = Field(min_length=1, max_length=500)
    fact_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class Mem0Diagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=64)


class CloudWriteOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    queued: bool = False
    memory_id: str | None = Field(default=None, max_length=200)
    diagnostic: Mem0Diagnostic | None = None


class CloudDeleteStatus(StrEnum):
    REMOVED = "removed"
    NOT_FOUND = "memory_not_found"
    FAILED = "failed"


class CloudDeleteOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: CloudDeleteStatus
    memory_id: str = Field(min_length=1, max_length=200)
    diagnostic: Mem0Diagnostic | None = None
