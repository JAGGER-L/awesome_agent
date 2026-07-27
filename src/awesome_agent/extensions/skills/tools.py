from __future__ import annotations

from typing import Never, cast

from pydantic import BaseModel, Field

from awesome_agent.core.tools import (
    ExpectedToolFailure,
    ToolArguments,
    ToolErrorCode,
    ToolInvocationDescription,
    ToolOutput,
    ToolSpec,
)
from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import ToolExecutionOrigin
from awesome_agent.core.tools.permissions import ToolCapability
from awesome_agent.core.tools.registry import ToolRegistry, ToolReplaySafety
from awesome_agent.extensions.skills.loader import (
    SkillLoader,
    SkillResourceError,
    SkillResourceErrorKind,
)
from awesome_agent.extensions.skills.models import SkillNotFound

_SKILL_FAILURES = {
    SkillResourceErrorKind.INVALID_ARGUMENTS: (
        ToolErrorCode.INVALID_ARGUMENTS,
        "Skill resource path is invalid.",
    ),
    SkillResourceErrorKind.NOT_FOUND: (
        ToolErrorCode.NOT_FOUND,
        "Skill or resource was not found.",
    ),
    SkillResourceErrorKind.CONFLICT: (
        ToolErrorCode.CONFLICT,
        "Skill changed after discovery.",
    ),
    SkillResourceErrorKind.PERMISSION_DENIED: (
        ToolErrorCode.PERMISSION_DENIED,
        "Skill resource could not be opened safely.",
    ),
    SkillResourceErrorKind.EXECUTION_FAILED: (
        ToolErrorCode.EXECUTION_FAILED,
        "Skill content could not be loaded.",
    ),
}


class LoadSkillArguments(ToolArguments):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")


class ReadSkillResourceArguments(ToolArguments):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    relative_path: str = Field(min_length=1, max_length=2_000)


def register_skill_tools(registry: ToolRegistry, loader: SkillLoader) -> None:
    def admit_load(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> None:
        options = cast(LoadSkillArguments, arguments)
        expected_identity = _expected_skill_identity(
            context,
            name=options.name,
            operation="load",
        )
        try:
            loader.admit_load(
                options.name,
                expected_identity=expected_identity,
            )
        except (SkillNotFound, SkillResourceError) as error:
            _raise_expected_failure(error)

    def admit_resource(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> None:
        options = cast(ReadSkillResourceArguments, arguments)
        expected_identity = _expected_skill_identity(
            context,
            name=options.name,
            operation="read",
        )
        try:
            loader.admit_resource(
                options.name,
                options.relative_path,
                expected_identity=expected_identity,
            )
        except (SkillNotFound, SkillResourceError) as error:
            _raise_expected_failure(error)

    def describe_load(arguments: BaseModel) -> ToolInvocationDescription:
        options = cast(LoadSkillArguments, arguments)
        return ToolInvocationDescription(
            verb="Load Skill",
            display_target=options.name,
            approval_operation="load",
            approval_target=options.name,
        )

    def describe_resource(arguments: BaseModel) -> ToolInvocationDescription:
        options = cast(ReadSkillResourceArguments, arguments)
        target = f"{options.name}/{options.relative_path}"
        return ToolInvocationDescription(
            verb="Read Skill Resource",
            display_target=target[:2_000],
            approval_operation="read",
            approval_target=target[:8_000],
        )

    async def load_skill(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        options = cast(LoadSkillArguments, arguments)
        expected_identity = _expected_skill_identity(
            context,
            name=options.name,
            operation="load",
        )
        try:
            loaded = loader.load(
                options.name,
                expected_identity=expected_identity,
            )
        except (SkillNotFound, SkillResourceError) as error:
            _raise_expected_failure(error)
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
        options = cast(ReadSkillResourceArguments, arguments)
        expected_identity = _expected_skill_identity(
            context,
            name=options.name,
            operation="read",
        )
        try:
            resource = loader.read_resource(
                options.name,
                options.relative_path,
                expected_identity=expected_identity,
                token_limit=5_000,
            )
        except (SkillNotFound, SkillResourceError) as error:
            _raise_expected_failure(error)
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
            capability=ToolCapability.CONTEXT_READ,
            read_only=True,
        ),
        input_model=LoadSkillArguments,
        handler=load_skill,
        describe=describe_load,
        admit=admit_load,
        replay_safety=ToolReplaySafety.REPLAYABLE,
    )
    registry.register(
        spec=ToolSpec(
            name="read_skill_resource",
            description="Read one bounded text resource from a Skill package",
            input_schema=ReadSkillResourceArguments.model_json_schema(),
            capability=ToolCapability.CONTEXT_READ,
            read_only=True,
        ),
        input_model=ReadSkillResourceArguments,
        handler=read_resource,
        describe=describe_resource,
        admit=admit_resource,
        replay_safety=ToolReplaySafety.REPLAYABLE,
    )


def _expected_skill_identity(
    context: ToolExecutionContext,
    *,
    name: str,
    operation: str,
) -> str:
    mode = context.skill_mode
    mode_allows_operation = (mode == "auto" and operation in {"load", "read"}) or (
        mode not in {"auto", "off", "direct"} and operation == "read" and name == mode
    )
    if context.origin is not ToolExecutionOrigin.AGENT or not mode_allows_operation:
        _raise_skill_scope_denied()
    grant = context.resource_grant(
        capability=ToolCapability.CONTEXT_READ.value,
        resource_type="skill",
        resource_id=name,
        operation=operation,
    )
    if grant is None:
        _raise_skill_scope_denied()
    return grant.identity


def _raise_skill_scope_denied() -> Never:
    raise ExpectedToolFailure(
        ToolErrorCode.PERMISSION_DENIED,
        "Skill access is unavailable in the frozen Turn context.",
    )


def _raise_expected_failure(error: SkillNotFound | SkillResourceError) -> Never:
    if isinstance(error, SkillNotFound):
        raise ExpectedToolFailure(
            ToolErrorCode.NOT_FOUND,
            "Skill was not found.",
        ) from error
    code, message = _SKILL_FAILURES[error.kind]
    raise ExpectedToolFailure(code, message) from error
