from __future__ import annotations

import pytest

from awesome_agent.extensions.skills import ResolvedSkill, SkillRuntimeView
from awesome_agent.modeling import ModelRequest, ToolDefinition, UserMessage
from awesome_agent.runtime.agent_loop import MiddlewareContext
from awesome_agent.runtime.agent_loop.skill_context_middleware import (
    SkillContextMiddleware,
)


@pytest.mark.asyncio
async def test_skill_context_injected_before_model_call() -> None:
    request = ModelRequest(messages=[UserMessage(content="inspect")], tools=[])
    context = _context_with_skill("repository-inspection", "Use bounded reads.")

    updated = await SkillContextMiddleware().before_model_call(request, context)

    assert "Use bounded reads." in updated.messages[0].content
    assert updated.messages[1] == request.messages[0]


@pytest.mark.asyncio
async def test_skill_context_does_not_add_unexposed_tool_schema() -> None:
    request = ModelRequest(
        messages=[UserMessage(content="inspect")],
        tools=[
            ToolDefinition(
                name="repo.search",
                description="Search repository",
                input_schema={"type": "object"},
            )
        ],
    )
    context = _context_with_skill(
        "repository-inspection",
        "Use repo.read only when exposed.",
        requested_tools=["repo.read"],
    )

    updated = await SkillContextMiddleware().before_model_call(request, context)

    assert [tool.name for tool in updated.tools] == ["repo.search"]


def _context_with_skill(
    skill_id: str,
    instructions: str,
    *,
    requested_tools: list[str] | None = None,
) -> MiddlewareContext:
    return MiddlewareContext(
        run_id="run",
        agent_id="agent",
        runtime_route="team-role",
        messages=[UserMessage(content="inspect")],
        skill_runtime_view=SkillRuntimeView(
            skill_ids=[skill_id],
            skills=[
                ResolvedSkill(
                    id=skill_id,
                    version="1",
                    instructions=instructions,
                )
            ],
            requested_tools=requested_tools or [],
        ),
    )

