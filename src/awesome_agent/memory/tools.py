from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import BaseModel, Field, field_validator

from awesome_agent.config.resource_lock import (
    ResourceLockTimeout,
    ResourceLockUnavailable,
)
from awesome_agent.core.cancellation import run_cancellation_safe_blocking_call
from awesome_agent.core.tools.context import ToolExecutionContext, ToolHandler
from awesome_agent.core.tools.contracts import (
    ToolArguments,
    ToolErrorCode,
    ToolExecutionOrigin,
    ToolInvocationDescription,
    ToolOutput,
    ToolSpec,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.registry import (
    RegisteredTool,
    ToolAdmitter,
    ToolRegistry,
    ToolReplaySafety,
)
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
_MEMORY_HANDLER_CANCELLATION_GRACE_SECONDS = 23.0


class _MemoryArguments(ToolArguments):
    scope: MemoryScope = Field(strict=False)

    @field_validator("scope", mode="before")
    @classmethod
    def validate_scope_type(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("memory scope must be a string")
        return value


class MemoryListArguments(_MemoryArguments):
    pass


class MemoryAddArguments(_MemoryArguments):
    content: str = Field(min_length=1, max_length=2_000)
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class MemoryReplaceArguments(MemoryAddArguments):
    entry_id: str = Field(pattern=r"^memory_[a-f0-9]{32}$")


class MemoryRemoveArguments(_MemoryArguments):
    entry_id: str = Field(pattern=r"^memory_[a-f0-9]{32}$")
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def refresh_local_memory_tools(
    registry: ToolRegistry,
    service: LocalMemoryService,
    *,
    enabled: bool | None = None,
) -> None:
    selected_enabled = service.enabled if enabled is None else enabled
    registry.replace_exact_set(
        MEMORY_TOOL_NAMES,
        _registered_local_memory_tools(service) if selected_enabled else (),
    )


def validate_local_memory_tools(
    registry: ToolRegistry,
    service: LocalMemoryService,
    *,
    enabled: bool,
) -> None:
    """Preflight one local-memory visibility change without publishing it."""

    registry.validate_exact_set(
        MEMORY_TOOL_NAMES,
        _registered_local_memory_tools(service) if enabled else (),
    )


def _registered_local_memory_tools(
    service: LocalMemoryService,
) -> tuple[RegisteredTool, ...]:
    read_admit = _memory_admitter(service, mutation=False)
    mutation_admit = _memory_admitter(service, mutation=True)
    return (
        RegisteredTool(
            spec=ToolSpec(
                name="memory_list",
                description=(
                    "List visible local memory entries and their current hash."
                ),
                input_schema=MemoryListArguments.model_json_schema(),
                capability="memory.read",
                read_only=True,
                display_metadata={"category": "agent_core"},
            ),
            input_model=MemoryListArguments,
            handler=_list_handler(service),
            describe=_memory_describer("List Memory", "list"),
            admit=read_admit,
            replay_safety=ToolReplaySafety.REPLAYABLE,
        ),
        RegisteredTool(
            spec=ToolSpec(
                name="memory_add",
                description=(
                    "Add local memory only when the current user explicitly asks "
                    "to remember it. Requires the last observed hash."
                ),
                input_schema=MemoryAddArguments.model_json_schema(),
                capability="memory.write",
                read_only=False,
                display_metadata={"category": "agent_core"},
            ),
            input_model=MemoryAddArguments,
            handler=_add_handler(service),
            describe=_memory_describer("Add Memory", "add"),
            admit=mutation_admit,
            replay_safety=ToolReplaySafety.REPLAYABLE,
            cancellation_grace_seconds=_MEMORY_HANDLER_CANCELLATION_GRACE_SECONDS,
        ),
        RegisteredTool(
            spec=ToolSpec(
                name="memory_replace",
                description=(
                    "Replace a local memory entry only on an explicit current-user "
                    "request. Requires its ID and last observed hash."
                ),
                input_schema=MemoryReplaceArguments.model_json_schema(),
                capability="memory.write",
                read_only=False,
                display_metadata={"category": "agent_core"},
            ),
            input_model=MemoryReplaceArguments,
            handler=_replace_handler(service),
            describe=_memory_describer(
                "Replace Memory",
                "replace",
                target_field="entry_id",
            ),
            admit=mutation_admit,
            replay_safety=ToolReplaySafety.REPLAYABLE,
            cancellation_grace_seconds=_MEMORY_HANDLER_CANCELLATION_GRACE_SECONDS,
        ),
        RegisteredTool(
            spec=ToolSpec(
                name="memory_remove",
                description=(
                    "Remove a local memory entry only on an explicit current-user "
                    "request. Requires its ID and last observed hash."
                ),
                input_schema=MemoryRemoveArguments.model_json_schema(),
                capability="memory.write",
                read_only=False,
                display_metadata={"category": "agent_core"},
            ),
            input_model=MemoryRemoveArguments,
            handler=_remove_handler(service),
            describe=_memory_describer(
                "Remove Memory",
                "remove",
                target_field="entry_id",
            ),
            admit=mutation_admit,
            replay_safety=ToolReplaySafety.REPLAYABLE,
            cancellation_grace_seconds=_MEMORY_HANDLER_CANCELLATION_GRACE_SECONDS,
        ),
    )


def _memory_admitter(
    service: LocalMemoryService,
    *,
    mutation: bool,
) -> ToolAdmitter:
    def admit(arguments: BaseModel, context: ToolExecutionContext) -> None:
        del arguments
        if mutation:
            _require_mutation_context(service, context)
        else:
            _require_workspace(service, context)

    return admit


def _memory_describer(
    verb: str,
    operation: str,
    *,
    target_field: str | None = None,
) -> Callable[[BaseModel], ToolInvocationDescription]:
    def describe(arguments: BaseModel) -> ToolInvocationDescription:
        if target_field is None:
            scope = getattr(arguments, "scope", None)
            target = scope.value if isinstance(scope, MemoryScope) else "memory"
        else:
            candidate = getattr(arguments, target_field, None)
            target = candidate if isinstance(candidate, str) else "memory"
        return ToolInvocationDescription(
            verb=verb,
            display_target=target[:2_000],
            approval_operation=operation,
            approval_target=target[:8_000],
        )

    return describe


def _list_handler(service: LocalMemoryService) -> ToolHandler:
    async def handler(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        assert isinstance(arguments, MemoryListArguments)
        _require_workspace(service, context)
        document = await _run_memory_call(lambda: service.snapshot(arguments.scope))
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
        result = await _run_memory_call(
            lambda: service.add(
                arguments.scope,
                arguments.content,
                expected_hash=arguments.expected_hash,
            )
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
        result = await _run_memory_call(
            lambda: service.replace(
                arguments.scope,
                arguments.entry_id,
                arguments.content,
                expected_hash=arguments.expected_hash,
            )
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
        result = await _run_memory_call(
            lambda: service.remove(
                arguments.scope,
                arguments.entry_id,
                expected_hash=arguments.expected_hash,
            )
        )
        return _mutation_output(result)

    return handler


async def _run_memory_call[ResultT](call: Callable[[], ResultT]) -> ResultT:
    try:
        return await run_cancellation_safe_blocking_call(call)
    except ResourceLockTimeout as error:
        raise ExpectedToolFailure(
            ToolErrorCode.TIMEOUT,
            "Local memory is being changed by another Awesome process.",
            retryable=True,
        ) from error
    except ResourceLockUnavailable as error:
        raise ExpectedToolFailure(
            ToolErrorCode.STATE_UNAVAILABLE,
            "Local memory cannot be accessed safely.",
            retryable=False,
        ) from error


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
