from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class PlannedTask(BaseModel):
    title: str
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)


class LeaderPlan(BaseModel):
    objective: str
    use_team: bool = False
    reasoning: str
    tasks: list[PlannedTask] = Field(min_length=1)


class PlanRevision(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    number: int = Field(ge=1)
    reason: str
    plan: LeaderPlan
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanHistory(BaseModel):
    run_id: UUID
    revisions: list[PlanRevision] = Field(default_factory=list)

    @property
    def current(self) -> PlanRevision | None:
        return self.revisions[-1] if self.revisions else None

    def revise(self, plan: LeaderPlan, *, reason: str) -> PlanRevision:
        revision = PlanRevision(
            number=len(self.revisions) + 1,
            reason=reason,
            plan=plan,
        )
        self.revisions.append(revision)
        return revision

    def export(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
