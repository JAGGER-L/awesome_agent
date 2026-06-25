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
