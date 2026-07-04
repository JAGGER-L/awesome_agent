from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from awesome_agent.attachments.service import AttachmentService
from awesome_agent.domain.enums import RiskLevel
from awesome_agent.tools.models import ToolInvocation, ToolResult, ToolSpec
from awesome_agent.tools.registry import ToolRegistry


class AttachmentListArguments(BaseModel):
    pass


class AttachmentReadArguments(BaseModel):
    attachment_id: UUID
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=200, ge=1, le=500)
    max_chars: int = Field(default=30_000, ge=1, le=30_000)


def register_attachment_tools(
    registry: ToolRegistry,
    service: AttachmentService,
) -> None:
    registry.register(
        ToolSpec(
            name="attachment.list",
            description="List files attached to the current conversation Run.",
            risk_level=RiskLevel.LOW,
            required_capabilities={"attachment:read"},
            sandbox_required=False,
            input_schema=AttachmentListArguments.model_json_schema(),
        ),
        lambda invocation, _progress: _list(service, invocation),
    )
    registry.register(
        ToolSpec(
            name="attachment.read",
            description=(
                "Read a bounded UTF-8 text range from a current Run attachment."
            ),
            risk_level=RiskLevel.LOW,
            required_capabilities={"attachment:read"},
            sandbox_required=False,
            input_schema=AttachmentReadArguments.model_json_schema(),
        ),
        lambda invocation, _progress: _read(service, invocation),
    )


async def _list(service: AttachmentService, invocation: ToolInvocation) -> ToolResult:
    if invocation.run_id is None:
        raise ValueError("attachment_not_bound_to_run")
    items = await service.list_for_tool(run_id=invocation.run_id)
    return ToolResult(
        invocation_id=invocation.id,
        output={"items": [item.snapshot() for item in items]},
    )


async def _read(service: AttachmentService, invocation: ToolInvocation) -> ToolResult:
    if invocation.run_id is None:
        raise ValueError("attachment_not_bound_to_run")
    arguments = AttachmentReadArguments.model_validate(invocation.arguments)
    result = await service.read_for_tool(
        run_id=invocation.run_id,
        attachment_id=arguments.attachment_id,
        start_line=arguments.start_line,
        max_lines=arguments.max_lines,
        max_chars=arguments.max_chars,
    )
    return ToolResult(
        invocation_id=invocation.id,
        output=result.model_dump(mode="json"),
    )
