from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel

from awesome_agent.core.tools.context import ToolHandler
from awesome_agent.core.tools.contracts import ToolSpec
from awesome_agent.core.tools.errors import DuplicateToolName as DuplicateToolName


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    spec: ToolSpec
    input_model: type[BaseModel]
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._items: dict[str, RegisteredTool] = {}

    def register(
        self,
        *,
        spec: ToolSpec,
        input_model: type[BaseModel],
        handler: ToolHandler,
    ) -> None:
        if spec.name in self._items:
            raise DuplicateToolName(spec.name)
        self._items[spec.name] = RegisteredTool(spec, input_model, handler)

    def resolve(self, name: str) -> RegisteredTool | None:
        return self._items.get(name)

    def specifications(self) -> tuple[ToolSpec, ...]:
        return tuple(self._items[name].spec for name in sorted(self._items))

    def unregister(self, name: str) -> None:
        self._items.pop(name, None)

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
        self._items = updated

    def remove_namespace(self, namespace: str) -> None:
        prefix = f"{namespace}."
        self._items = {
            name: tool
            for name, tool in self._items.items()
            if not name.startswith(prefix)
        }
