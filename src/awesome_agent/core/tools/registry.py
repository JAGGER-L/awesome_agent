from __future__ import annotations

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
