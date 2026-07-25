from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ChangeLifecycle(StrEnum):
    OPEN = "open"
    APPLIED = "applied"
    UNDONE = "undone"


class ChangeReversibility(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


class FileChangeKind(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


class FileNodeType(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


class FileChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    mutation_id: str | None = None
    path: str = Field(min_length=1, max_length=1_000)
    kind: FileChangeKind
    # Retained as the legacy/display type for existing Schema 7 JSON payloads.
    node_type: FileNodeType
    before_node_type: FileNodeType | None = None
    after_node_type: FileNodeType | None = None
    before_hash: str | None = None
    after_hash: str | None = None
    before_blob: str | None = None
    after_blob: str | None = None
    before_mode: int | None = None
    after_mode: int | None = None

    @property
    def resolved_before_node_type(self) -> FileNodeType | None:
        if self.before_hash is None:
            return None
        return self.before_node_type or self.node_type

    @property
    def resolved_after_node_type(self) -> FileNodeType | None:
        if self.after_hash is None:
            return None
        return self.after_node_type or self.node_type


class ExecuteObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    command: str = Field(min_length=1, max_length=8_000)
    observed_paths: list[str] = Field(default_factory=list, max_length=1_000)


class ChangeSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    session_id: str
    turn_id: str | None
    workspace_key: str
    lifecycle: ChangeLifecycle
    reversibility: ChangeReversibility
    files: list[FileChange] = Field(default_factory=list)
    execute: list[ExecuteObservation] = Field(default_factory=list)
    created_at: datetime
    sealed_at: datetime | None = None
