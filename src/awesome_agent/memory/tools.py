from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.core.tools.context import ToolExecutionContext, ToolHandler
from awesome_agent.core.tools.contracts import (
    ToolErrorCode,
    ToolExecutionOrigin,
    ToolOutput,
    ToolSpec,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.memory.models import (
    MemoryMutationResult,
    MemoryMutationStatus,
    MemoryScope,
)
from awesome_agent.memory.service import LocalMemoryService

MEMORY_TOOL_NAMES = (
    "memory_add",
    "memory_list",
    "memory_remove",
    "memory_replace",
)


class MemoryListArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: MemoryScope


class MemoryAddArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: MemoryScope
    content: str = Field(min_length=1, max_length=2_000)
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class MemoryReplaceArguments(MemoryAddArguments):
    entry_id: str = Field(pattern=r"^memory_[a-f0-9]{32}$")


class MemoryRemoveArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: MemoryScope
    entry_id: str = Field(pattern=r"^memory_[a-f0-9]{32}$")
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def refresh_local_memory_tools(
    registry: ToolRegistry,
    service: LocalMemoryService,
) -> None:
    for name in MEMORY_TOOL_NAMES:
        registry.unregister(name)
    if not service.enabled:
        return
    registry.register(
        spec=ToolSpec(
            name="memory_list",
            description="List visible local memory entries and their current hash.",
            input_schema=MemoryListArguments.model_json_schema(),
            read_only=True,
            display_metadata={"category": "agent_core"},
        ),
        input_model=MemoryListArguments,
        handler=_list_handler(service),
    )
    registry.register(
        spec=ToolSpec(
            name="memory_add",
            description=(
                "Add local memory only when the current user explicitly asks "
                "to remember it. Requires the last observed hash."
            ),
            input_schema=MemoryAddArguments.model_json_schema(),
            read_only=False,
            display_metadata={"category": "agent_core"},
        ),
        input_model=MemoryAddArguments,
        handler=_add_handler(service),
    )
    registry.register(
        spec=ToolSpec(
            name="memory_replace",
            description=(
                "Replace a local memory entry only on an explicit current-user "
                "request. Requires its ID and last observed hash."
            ),
            input_schema=MemoryReplaceArguments.model_json_schema(),
            read_only=False,
            display_metadata={"category": "agent_core"},
        ),
        input_model=MemoryReplaceArguments,
        handler=_replace_handler(service),
    )
    registry.register(
        spec=ToolSpec(
            name="memory_remove",
            description=(
                "Remove a local memory entry only on an explicit current-user "
                "request. Requires its ID and last observed hash."
            ),
            input_schema=MemoryRemoveArguments.model_json_schema(),
            read_only=False,
            display_metadata={"category": "agent_core"},
        ),
        input_model=MemoryRemoveArguments,
        handler=_remove_handler(service),
    )


def _list_handler(service: LocalMemoryService) -> ToolHandler:
    async def handler(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        assert isinstance(arguments, MemoryListArguments)
        _require_workspace(service, context)
        document = service.snapshot(arguments.scope)
        content = json.dumps(
            {
                "scope": arguments.scope.value,
                "content_hash": document.content_hash,
                "entries": [
                    {"id": entry.id, "content": entry.content}
                    for entry in document.entries
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return ToolOutput(content=content)

    return handler


def _add_handler(service: LocalMemoryService) -> ToolHandler:
    async def handler(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        assert isinstance(arguments, MemoryAddArguments)
        _require_mutation_context(service, context)
        result = service.add(
            arguments.scope,
            arguments.content,
            expected_hash=arguments.expected_hash,
        )
        return _mutation_output(result)

    return handler


def _replace_handler(service: LocalMemoryService) -> ToolHandler:
    async def handler(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        assert isinstance(arguments, MemoryReplaceArguments)
        _require_mutation_context(service, context)
        result = service.replace(
            arguments.scope,
            arguments.entry_id,
            arguments.content,
            expected_hash=arguments.expected_hash,
        )
        return _mutation_output(result)

    return handler


def _remove_handler(service: LocalMemoryService) -> ToolHandler:
    async def handler(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        assert isinstance(arguments, MemoryRemoveArguments)
        _require_mutation_context(service, context)
        result = service.remove(
            arguments.scope,
            arguments.entry_id,
            expected_hash=arguments.expected_hash,
        )
        return _mutation_output(result)

    return handler


def _require_workspace(
    service: LocalMemoryService,
    context: ToolExecutionContext,
) -> None:
    if context.workspace.key != service.workspace_key:
        raise ExpectedToolFailure(
            ToolErrorCode.PERMISSION_DENIED,
            "Local memory belongs to a different workspace Host.",
        )


def _require_mutation_context(
    service: LocalMemoryService,
    context: ToolExecutionContext,
) -> None:
    _require_workspace(service, context)
    if (
        context.origin is not ToolExecutionOrigin.AGENT
        or context.turn_id is None
        or not context.turn_active
    ):
        raise ExpectedToolFailure(
            ToolErrorCode.PERMISSION_DENIED,
            "Local memory mutation requires an active Agent Turn.",
        )


def _mutation_output(result: MemoryMutationResult) -> ToolOutput:
    if result.status in {
        MemoryMutationStatus.ADDED,
        MemoryMutationStatus.REPLACED,
        MemoryMutationStatus.REMOVED,
    }:
        return ToolOutput(
            content=json.dumps(
                {
                    "status": result.status.value,
                    "entry_id": result.entry_id,
                    "content_hash": result.content_hash,
                },
                separators=(",", ":"),
            )
        )
    failures = {
        MemoryMutationStatus.DISABLED: (
            ToolErrorCode.MEMORY_DISABLED,
            "Local file memory is disabled.",
            False,
        ),
        MemoryMutationStatus.CONFLICT: (
            ToolErrorCode.MEMORY_CONFLICT,
            "Local memory changed; list it again before retrying.",
            True,
        ),
        MemoryMutationStatus.NOT_FOUND: (
            ToolErrorCode.NOT_FOUND,
            "Local memory entry was not found.",
            False,
        ),
        MemoryMutationStatus.REJECTED: (
            ToolErrorCode.MEMORY_REJECTED,
            "Memory content was rejected by policy.",
            False,
        ),
    }
    code, message, retryable = failures[result.status]
    raise ExpectedToolFailure(
        code,
        message,
        retryable=retryable,
        metadata={"content_hash": result.content_hash},
    )
