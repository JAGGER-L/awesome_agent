from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from awesome_agent.core.changes.models import (
    ChangeSet,
    FileChangeKind,
    FileNodeType,
)


class PendingMutation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    change_set_id: str
    workspace_key: str
    relative_path: str
    kind: FileChangeKind
    # Retained as the legacy/display type for existing Schema 7 rows.
    node_type: FileNodeType
    before_node_type: FileNodeType | None = None
    intended_after_node_type: FileNodeType | None = None
    before_hash: str | None
    before_blob: str | None
    before_mode: int | None
    intended_after_hash: str | None
    intended_after_blob: str | None
    intended_after_mode: int | None
    created_at: datetime

    @property
    def resolved_before_node_type(self) -> FileNodeType | None:
        if self.before_hash is None:
            return None
        return self.before_node_type or self.node_type

    @property
    def resolved_intended_after_node_type(self) -> FileNodeType | None:
        if self.intended_after_hash is None:
            return None
        return self.intended_after_node_type or self.node_type


class ChangeSetStore(Protocol):
    def save(self, change_set: ChangeSet) -> None: ...

    def get(self, change_set_id: str) -> ChangeSet | None: ...

    def latest(self, workspace_key: str) -> ChangeSet | None: ...

    def list_open(self, workspace_key: str) -> list[ChangeSet]: ...

    def save_pending(self, pending: PendingMutation) -> None: ...

    def list_pending(self) -> list[PendingMutation]: ...

    def delete_pending(self, pending_id: str) -> None: ...


class ChangeBlobStore(Protocol):
    def put(self, content: bytes) -> str: ...

    def get(self, digest: str) -> bytes: ...
