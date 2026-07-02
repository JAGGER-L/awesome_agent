from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

LOGICAL_THREAD_WORKSPACE = "/mnt/user-data/workspace/"


class Thread(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str
    context_kind: str = "workspace"
    context_path: str | None = None
    repository_id: UUID | None = None
    default_model: str | None = None
    sandbox_profile: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def host_workspace_path(self, *, home: Path | None = None) -> Path:
        root = home or Path.home()
        return root / ".awesome-agent" / "threads" / str(self.id) / "workspace"

    @property
    def logical_workspace_path(self) -> str:
        return LOGICAL_THREAD_WORKSPACE

    def api_payload(self, *, home: Path | None = None) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload["host_workspace_path"] = str(self.host_workspace_path(home=home))
        payload["logical_workspace_path"] = self.logical_workspace_path
        return payload
