from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from awesome_agent.core.tools.context import ToolHandler
from awesome_agent.core.tools.contracts import ToolSpec
from awesome_agent.core.tools.errors import DuplicateToolName as DuplicateToolName

type ToolTimeoutResolver = Callable[[BaseModel], float]

MAX_REGISTERED_TOOLS = 128
MAX_REGISTERED_TOOL_CATALOG_BYTES = 1024 * 1024
MAX_REGISTERED_TOOL_NAME_CHARS = 128


class ToolRegistryLimitError(ValueError):
    """A candidate Registry snapshot cannot be published safely."""


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    spec: ToolSpec
    input_model: type[BaseModel]
    handler: ToolHandler
    timeout_resolver: ToolTimeoutResolver | None = None
    cancellation_grace_seconds: float | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._items: dict[str, RegisteredTool] = {}

    def register(
        self,
        *,
        spec: ToolSpec,
        input_model: type[BaseModel],
        handler: ToolHandler,
        timeout_resolver: ToolTimeoutResolver | None = None,
        cancellation_grace_seconds: float | None = None,
    ) -> None:
        if spec.name in self._items:
            raise DuplicateToolName(spec.name)
        updated = dict(self._items)
        updated[spec.name] = RegisteredTool(
            spec,
            input_model,
            handler,
            timeout_resolver,
            cancellation_grace_seconds,
        )
        _validate_snapshot(updated)
        self._items = updated

    def resolve(self, name: str) -> RegisteredTool | None:
        return self._items.get(name)

    def specifications(self) -> tuple[ToolSpec, ...]:
        return tuple(self._items[name].spec for name in sorted(self._items))

    def unregister(self, name: str) -> None:
        self._items.pop(name, None)

    def validate_exact_set(
        self,
        managed_names: tuple[str, ...],
        tools: tuple[RegisteredTool, ...],
    ) -> None:
        """Validate replacing one explicit tool set without publishing it."""

        _validate_snapshot(self._exact_set_snapshot(managed_names, tools))

    def replace_exact_set(
        self,
        managed_names: tuple[str, ...],
        tools: tuple[RegisteredTool, ...],
    ) -> None:
        """Publish an all-or-nothing replacement for explicit managed names."""

        updated = self._exact_set_snapshot(managed_names, tools)
        _validate_snapshot(updated)
        self._items = updated

    def replace_namespace(
        self,
        namespace: str,
        tools: tuple[RegisteredTool, ...],
    ) -> None:
        if (
            re.fullmatch(
                r"(?:mcp|user)\.[a-z][a-z0-9_-]*",
                namespace,
            )
            is None
        ):
            raise ValueError(f"Invalid tool namespace: {namespace}")
        prefix = f"{namespace}."
        replacements = {tool.spec.name: tool for tool in tools}
        if len(replacements) != len(tools):
            raise DuplicateToolName(namespace)
        if any(not name.startswith(prefix) for name in replacements):
            raise ValueError(f"Tool does not belong to namespace: {namespace}")
        updated = {
            name: tool
            for name, tool in self._items.items()
            if not name.startswith(prefix)
        }
        collisions = replacements.keys() & updated.keys()
        if collisions:
            raise DuplicateToolName(sorted(collisions)[0])
        updated.update(replacements)
        _validate_snapshot(updated)
        self._items = updated

    def remove_namespace(self, namespace: str) -> None:
        prefix = f"{namespace}."
        self._items = {
            name: tool
            for name, tool in self._items.items()
            if not name.startswith(prefix)
        }

    def _exact_set_snapshot(
        self,
        managed_names: tuple[str, ...],
        tools: tuple[RegisteredTool, ...],
    ) -> dict[str, RegisteredTool]:
        if not managed_names or any(
            not isinstance(name, str) for name in managed_names
        ):
            raise ValueError("Managed tool names must be non-empty strings")
        managed = frozenset(managed_names)
        if len(managed) != len(managed_names):
            raise ValueError("Managed tool names must be non-empty and unique")
        replacement_names: list[str] = []
        for tool in tools:
            if not isinstance(tool, RegisteredTool) or not isinstance(
                tool.spec, ToolSpec
            ):
                raise ToolRegistryLimitError(
                    "Tool registry contains an invalid runtime contract"
                )
            replacement_names.append(tool.spec.name)
        replacements = {tool.spec.name: tool for tool in tools}
        if len(replacements) != len(replacement_names):
            duplicate = next(
                name
                for index, name in enumerate(replacement_names)
                if name in replacement_names[:index]
            )
            raise DuplicateToolName(duplicate)
        if any(name not in managed for name in replacements):
            raise ValueError("Replacement tool is outside the managed exact set")
        updated = {
            name: tool for name, tool in self._items.items() if name not in managed
        }
        updated.update(replacements)
        return updated


def _validate_snapshot(items: dict[str, RegisteredTool]) -> None:
    if len(items) > MAX_REGISTERED_TOOLS:
        raise ToolRegistryLimitError(
            "Tool registry exceeds the 128-tool aggregate limit"
        )
    if any(
        not isinstance(name, str) or len(name) > MAX_REGISTERED_TOOL_NAME_CHARS
        for name in items
    ):
        raise ToolRegistryLimitError(
            "Tool registry contains a name outside downstream limits"
        )

    total_bytes = 2 + max(0, len(items) - 1)
    for name in sorted(items):
        registered = items[name]
        _validate_registered_tool(name, registered)
        grace = registered.cancellation_grace_seconds
        if grace is not None and (
            isinstance(grace, bool)
            or not isinstance(grace, (int, float))
            or grace <= 0
            or not math.isfinite(grace)
        ):
            raise ToolRegistryLimitError(
                "Tool registry contains an invalid cancellation grace"
            )
        spec = registered.spec
        payload = {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
        }
        try:
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            encoded = serialized.encode("utf-8")
        except (TypeError, ValueError, RecursionError) as error:
            raise ToolRegistryLimitError(
                "Tool registry contains an unsafe catalog contract"
            ) from error
        _validate_tool_spec(spec)
        total_bytes += len(encoded)
        if total_bytes > MAX_REGISTERED_TOOL_CATALOG_BYTES:
            raise ToolRegistryLimitError(
                "Tool registry exceeds the 1 MiB aggregate catalog limit"
            )


def _validate_registered_tool(name: str, registered: RegisteredTool) -> None:
    if not isinstance(registered, RegisteredTool):
        raise ToolRegistryLimitError(
            "Tool registry contains an invalid runtime contract"
        )
    spec = registered.spec
    if not isinstance(spec, ToolSpec) or spec.name != name:
        raise ToolRegistryLimitError(
            "Tool registry contains an invalid runtime contract"
        )
    input_model = registered.input_model
    if (
        not isinstance(input_model, type)
        or not issubclass(input_model, BaseModel)
        or not callable(registered.handler)
        or (
            registered.timeout_resolver is not None
            and not callable(registered.timeout_resolver)
        )
    ):
        raise ToolRegistryLimitError(
            "Tool registry contains an invalid runtime contract"
        )


def _validate_tool_spec(spec: ToolSpec) -> None:
    try:
        ToolSpec.model_validate(spec.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError, RecursionError) as error:
        raise ToolRegistryLimitError(
            "Tool registry contains an invalid runtime contract"
        ) from error
