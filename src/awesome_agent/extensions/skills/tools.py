from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from awesome_agent.core.tools import ToolOutput, ToolSpec
from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.permissions import ToolCapability
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.extensions.skills.loader import SkillLoader


class LoadSkillArguments(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")


class ReadSkillResourceArguments(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    relative_path: str = Field(min_length=1, max_length=2_000)


def register_skill_tools(registry: ToolRegistry, loader: SkillLoader) -> None:
    async def load_skill(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        del context
        options = cast(LoadSkillArguments, arguments)
        loaded = loader.load(options.name)
        return ToolOutput(
            content=loaded.body,
            metadata={
                "skill": loaded.descriptor.name,
                "source": loaded.descriptor.source.value,
                "truncated": loaded.truncated,
                "allowed_tools": list(loaded.descriptor.allowed_tools),
            },
        )

    async def read_resource(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        del context
        options = cast(ReadSkillResourceArguments, arguments)
        resource = loader.read_resource(
            options.name,
            options.relative_path,
            token_limit=5_000,
        )
        return ToolOutput(
            content=resource.content,
            metadata={
                "skill": resource.skill_name,
                "path": resource.relative_path,
                "truncated": resource.truncated,
            },
        )

    registry.register(
        spec=ToolSpec(
            name="load_skill",
            description="Load bounded instructions for one selected Skill",
            input_schema=LoadSkillArguments.model_json_schema(),
            capability=ToolCapability.WORKSPACE_READ,
            read_only=True,
        ),
        input_model=LoadSkillArguments,
        handler=load_skill,
    )
    registry.register(
        spec=ToolSpec(
            name="read_skill_resource",
            description="Read one bounded text resource from a Skill package",
            input_schema=ReadSkillResourceArguments.model_json_schema(),
            capability=ToolCapability.WORKSPACE_READ,
            read_only=True,
        ),
        input_model=ReadSkillResourceArguments,
        handler=read_resource,
    )
