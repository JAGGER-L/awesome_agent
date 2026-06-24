from __future__ import annotations

from collections.abc import Awaitable, Callable

from awesome_agent.tools.models import (
    ToolInvocation,
    ToolProgress,
    ToolResult,
    ToolSpec,
)

ProgressCallback = Callable[[ToolProgress], Awaitable[None]]
ToolHandler = Callable[[ToolInvocation, ProgressCallback | None], Awaitable[ToolResult]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, ToolHandler]] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool is already registered: {spec.name}")
        self._tools[spec.name] = (spec, handler)

    def resolve(self, name: str) -> tuple[ToolSpec, ToolHandler]:
        try:
            return self._tools[name]
        except KeyError as error:
            raise KeyError(f"Unknown tool: {name}") from error

    def list_specs(self) -> list[ToolSpec]:
        return [spec for spec, _ in self._tools.values()]
