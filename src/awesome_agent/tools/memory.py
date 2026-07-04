from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.memory.models import MemoryTarget
from awesome_agent.memory.service import MemoryService
from awesome_agent.tools.models import ToolInvocation, ToolResult, ToolSpec
from awesome_agent.tools.registry import ToolRegistry


class MemoryManageArguments(BaseModel):
    action: Literal["add", "list", "delete"]
    target: MemoryTarget | None = None
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    memory_id: str | None = Field(default=None, max_length=128)
    source: Literal["explicit_user_request", "model_initiated"] | None = None


def register_memory_tools(registry: ToolRegistry, service: MemoryService) -> None:
    registry.register(
        ToolSpec(
            name="memory.manage",
            description=(
                "Add, list, or delete long-term memory entries in USER.md or MEMORY.md."
            ),
            risk_level=RiskLevel.LOW,
            sandbox_required=False,
            required_capabilities={"memory:manage"},
            input_schema=MemoryManageArguments.model_json_schema(),
        ),
        lambda invocation, _progress: _handle_memory_manage(service, invocation),
    )


async def _handle_memory_manage(
    service: MemoryService,
    invocation: ToolInvocation,
) -> ToolResult:
    arguments = MemoryManageArguments.model_validate(invocation.arguments)
    if arguments.action == "add":
        if arguments.target is None or arguments.content is None:
            raise ValueError("add requires target and content.")
        result = await service.add(
            target=arguments.target,
            content=arguments.content,
            source=arguments.source or "model_initiated",
            run_id=invocation.run_id,
            agent_id=invocation.agent_id,
        )
    elif arguments.action == "list":
        result = await service.list_entries(target=arguments.target)
    else:
        if arguments.target is None or arguments.memory_id is None:
            raise ValueError("delete requires target and memory_id.")
        result = await service.delete(
            target=arguments.target,
            memory_id=arguments.memory_id,
            run_id=invocation.run_id,
            agent_id=invocation.agent_id,
        )
    return ToolResult(
        invocation_id=invocation.id,
        output=result.model_dump(mode="json", exclude_none=True),
    )
