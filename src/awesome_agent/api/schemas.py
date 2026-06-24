from pydantic import BaseModel, Field


class CreateRunRequest(BaseModel):
    goal: str = Field(min_length=1)


class ApprovalDecisionRequest(BaseModel):
    approved: bool
