from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TeamReworkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_child_run_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=8)


class TeamVerificationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["passed", "rework_required", "failed"]
    summary: str = Field(min_length=1, max_length=4000)
    rework_requests: list[TeamReworkRequest] = Field(default_factory=list, max_length=6)
    failure_kind: str | None = Field(default=None, max_length=64)
    risks: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _validate_decision_shape(self) -> TeamVerificationDecision:
        if self.decision == "passed":
            if self.rework_requests:
                raise ValueError("passed decisions cannot contain rework requests")
            if self.failure_kind is not None:
                raise ValueError("passed decisions cannot contain failure_kind")
        if self.decision == "rework_required" and not self.rework_requests:
            raise ValueError("rework_required decisions need rework requests")
        if self.decision == "failed" and self.rework_requests:
            raise ValueError("failed decisions cannot contain rework requests")
        return self
