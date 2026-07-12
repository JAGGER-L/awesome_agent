from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Protocol, cast

from mcp.types import CallToolResult, TextContent, Tool
from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import ToolErrorCode, ToolOutput, ToolSpec
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.registry import RegisteredTool, ToolRegistry
from awesome_agent.extensions.mcp.manager import McpCallUncertain, McpUnavailable

_MAX_CONTENT_CHARS = 30_000


class McpCaller(Protocol):
    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> CallToolResult: ...


class _McpArguments(BaseModel):
    model_config = ConfigDict(extra="allow")

    json_schema: ClassVar[Mapping[str, object]] = {}

    @model_validator(mode="before")
    @classmethod
    def validate_schema(cls, value: object) -> object:
        _validate_schema_value(value, cls.json_schema, path="arguments")
        return value


class McpToolAdapter:
    def __init__(self, manager: McpCaller, server_id: str) -> None:
        self._manager = manager
        self._server_id = server_id

    def replace_registry_tools(
        self,
        registry: ToolRegistry,
        tools: tuple[Tool, ...],
    ) -> None:
        registered = tuple(self._registered(tool) for tool in tools)
        registry.replace_namespace(f"mcp.{self._server_id}", registered)

    def remove_registry_tools(self, registry: ToolRegistry) -> None:
        registry.remove_namespace(f"mcp.{self._server_id}")

    def _registered(self, tool: Tool) -> RegisteredTool:
        namespace_name = f"mcp.{self._server_id}.{tool.name}"
        schema = cast(dict[str, JsonValue], dict(tool.inputSchema))
        input_model = type(
            f"Mcp_{self._server_id}_{tool.name}_Arguments".replace("-", "_"),
            (_McpArguments,),
            {"json_schema": schema},
        )
        annotations = (
            {}
            if tool.annotations is None
            else cast(
                dict[str, JsonValue],
                tool.annotations.model_dump(by_alias=True, exclude_none=True),
            )
        )

        async def handler(
            arguments: BaseModel,
            context: ToolExecutionContext,
        ) -> ToolOutput:
            del context
            payload = cast(dict[str, object], arguments.model_dump())
            try:
                result = await self._manager.call_tool(
                    self._server_id,
                    tool.name,
                    payload,
                )
            except McpCallUncertain as error:
                raise ExpectedToolFailure(
                    ToolErrorCode.UNCERTAIN_OUTCOME,
                    "MCP call outcome is uncertain; choose retry or abort.",
                    retryable=False,
                    metadata={"recovery": ["retry", "abort"]},
                ) from error
            except McpUnavailable as error:
                raise ExpectedToolFailure(
                    ToolErrorCode.EXECUTION_FAILED,
                    "MCP server is unavailable.",
                    retryable=True,
                ) from error
            content, truncated = _bounded_content(result)
            if result.isError is True:
                raise ExpectedToolFailure(
                    ToolErrorCode.EXECUTION_FAILED,
                    content[:2_000] or "MCP tool reported an error.",
                )
            return ToolOutput(
                content=content,
                metadata={
                    "server_id": self._server_id,
                    "mcp_tool": tool.name,
                    "external_side_effect": "unknown",
                    "truncated": truncated,
                },
            )

        return RegisteredTool(
            spec=ToolSpec(
                name=namespace_name,
                description=tool.description or f"MCP tool {tool.name}",
                input_schema=schema,
                read_only=False,
                display_metadata={"mcp_annotations": annotations},
            ),
            input_model=input_model,
            handler=handler,
        )


def _bounded_content(result: CallToolResult) -> tuple[str, bool]:
    parts: list[str] = []
    for item in result.content:
        if isinstance(item, TextContent):
            parts.append(item.text)
        else:
            parts.append(f"[MCP content: {item.type}]")
    content = "\n".join(parts)
    if len(content) <= _MAX_CONTENT_CHARS:
        return content, False
    omitted = len(content) - 29_000
    marker = f"\n...[MCP output truncated: {omitted} characters omitted]...\n"
    return f"{content[:24_000]}{marker}{content[-5_000:]}", True


def _validate_schema_value(
    value: object,
    schema: Mapping[str, object],
    *,
    path: str,
) -> None:
    expected = schema.get("type")
    if isinstance(expected, str) and not _matches_type(value, expected):
        raise ValueError(f"{path} must have JSON type {expected}")
    choices = schema.get("enum")
    if isinstance(choices, list) and value not in choices:
        raise ValueError(f"{path} is not an allowed value")
    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            missing = [name for name in required if name not in value]
            if missing:
                raise ValueError(f"{path} is missing required properties")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            if schema.get("additionalProperties") is False:
                extras = value.keys() - properties.keys()
                if extras:
                    raise ValueError(f"{path} has additional properties")
            for name, child in properties.items():
                if name in value and isinstance(child, dict):
                    _validate_schema_value(
                        value[name],
                        cast(Mapping[str, object], child),
                        path=f"{path}.{name}",
                    )
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate_schema_value(
                    item,
                    cast(Mapping[str, object], items),
                    path=f"{path}[{index}]",
                )


def _matches_type(value: object, expected: str) -> bool:
    matches = {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, int | float) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }
    return matches.get(expected, True)
