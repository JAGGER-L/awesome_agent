from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from awesome_agent.domain.enums import RunIntent

READ_ONLY_TEAM_TOOLS = {
    "repo.status",
    "repo.list",
    "repo.search",
    "repo.read",
    "repo.instructions",
    "repo.diff",
}
WRITE_TEAM_TOOLS = {
    "repo.apply_patch",
    "shell.execute",
}
ALL_TEAM_TOOLS = READ_ONLY_TEAM_TOOLS | WRITE_TEAM_TOOLS


class TeamPlanTeammate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_profile: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=4000)
    allowed_tools: list[str] = Field(default_factory=list, max_length=12)
    deferred_tools: list[str] = Field(default_factory=list, max_length=12)
    allowed_skills: list[str] = Field(default_factory=list, max_length=20)
    can_write: bool
    can_delegate: bool
    max_subagents: int = Field(ge=0, le=3)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=8)

    @field_validator("role_profile")
    @classmethod
    def _role_profile_slug(cls, value: str) -> str:
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
        if any(char not in allowed for char in value):
            raise ValueError("role_profile must be a lowercase slug")
        return value

    @field_validator("allowed_tools", "deferred_tools")
    @classmethod
    def _known_tools(cls, values: list[str]) -> list[str]:
        unknown = [tool for tool in values if tool not in ALL_TEAM_TOOLS]
        if unknown:
            raise ValueError(f"unknown tools: {', '.join(sorted(unknown))}")
        return values

    @field_validator("acceptance_criteria")
    @classmethod
    def _bounded_criteria(cls, values: list[str]) -> list[str]:
        for item in values:
            if not item.strip():
                raise ValueError("acceptance criteria must be non-empty")
            if len(item) > 1000:
                raise ValueError("acceptance criteria entries are too long")
        return values

    @model_validator(mode="after")
    def _validate_delegation_and_tools(self) -> TeamPlanTeammate:
        if not set(self.deferred_tools).issubset(self.allowed_tools):
            raise ValueError("deferred_tools must be a subset of allowed_tools")
        if not self.can_delegate and self.max_subagents != 0:
            raise ValueError("max_subagents must be 0 when can_delegate is false")
        if self.can_delegate and self.max_subagents < 1:
            raise ValueError("delegating teammates need at least one subagent slot")
        if not self.can_write and any(
            tool in WRITE_TEAM_TOOLS for tool in self.allowed_tools
        ):
            raise ValueError("read-only teammates cannot receive write tools")
        return self


class TeamPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1, max_length=4000)
    teammates: list[TeamPlanTeammate] = Field(min_length=1, max_length=3)


def validate_team_plan_for_intent(
    plan: TeamPlan,
    *,
    intent: RunIntent,
) -> TeamPlan:
    if intent is RunIntent.READ_ONLY:
        for teammate in plan.teammates:
            if teammate.can_write:
                raise ValueError("read-only team plans cannot create writing teammates")
            if any(tool in WRITE_TEAM_TOOLS for tool in teammate.allowed_tools):
                raise ValueError("read-only team plans cannot grant write tools")
    return plan
