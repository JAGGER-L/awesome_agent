from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from awesome_agent.domain.enums import ApprovalDecision, RiskLevel


class ToolSpec(BaseModel):
    name: str
    version: str = "1"
    description: str
    risk_level: RiskLevel
    allowed_profiles: set[str] = Field(default_factory=set)
    required_capabilities: set[str] = Field(default_factory=set)
    sandbox_required: bool = True
    timeout_seconds: float = Field(default=60, gt=0)


class ToolProgress(BaseModel):
    tool_call_id: UUID
    message: str
    percent: float | None = Field(default=None, ge=0, le=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ToolInvocation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    tool_name: str
    agent_id: UUID
    profile: str
    capabilities: set[str] = Field(default_factory=set)
    arguments: dict[str, Any] = Field(default_factory=dict)
    approval_granted: bool = False


class ToolResult(BaseModel):
    invocation_id: UUID
    output: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequired(RuntimeError):
    def __init__(self, invocation: ToolInvocation) -> None:
        self.invocation = invocation
        super().__init__(f"Approval required for tool {invocation.tool_name}.")


class ToolDenied(RuntimeError):
    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Tool execution denied: {tool_name}.")


class ApprovalOutcome(BaseModel):
    decision: ApprovalDecision
    reason: str
