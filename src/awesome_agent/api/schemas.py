from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.domain.enums import RunIntent


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: UUID
    goal: str = Field(min_length=1)
    intent: RunIntent = RunIntent.MODIFYING


class ApprovalDecisionRequest(BaseModel):
    approved: bool


class DispatchResponse(BaseModel):
    status: str
    available_at: datetime
    worker_id: UUID | None
    worker_name: str | None
    fencing_token: int
    attempt: int
    lease_acquired_at: datetime | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    last_release_reason: str | None
    last_error: str | None
