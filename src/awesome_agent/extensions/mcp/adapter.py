from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar, Protocol, cast

from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.protocols import Validator
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import ToolErrorCode, ToolOutput, ToolSpec
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.registry import RegisteredTool, ToolRegistry
from awesome_agent.extensions.mcp.catalog import CompiledMcpTool, McpCatalog
from awesome_agent.extensions.mcp.manager import McpCallUncertain, McpUnavailable

_MAX_CONTENT_CHARS = 30_000
_MCP_EXECUTOR_TIMEOUT_SECONDS = 40.0


class McpCaller(Protocol):
    def catalog(self, server_id: str) -> McpCatalog: ...

    def bind_catalog_invalidator(
        self,
        server_id: str,
        invalidator: Callable[[], None],
    ) -> None: ...

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, object],
        *,
        generation: int,
    ) -> CallToolResult: ...


class _McpArguments(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_validator: ClassVar[Validator]

    @model_validator(mode="before")
    @classmethod
    def validate_schema(cls, value: object) -> object:
        try:
            cls.schema_validator.validate(cast(JsonValue, value))
        except JsonSchemaValidationError as error:
            raise ValueError("MCP arguments did not match the tool schema") from error
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
        catalog = self._manager.catalog(self._server_id)
        if tuple(tool.name for tool in tools) != tuple(
            item.tool.name for item in catalog.compiled_tools
        ):
            raise McpUnavailable("MCP catalog changed before registry synchronization")
        registered = tuple(
            self._registered(item, generation=catalog.generation)
            for item in catalog.compiled_tools
        )
        namespace = f"mcp.{self._server_id}"
        registry.replace_namespace(namespace, registered)
        self._manager.bind_catalog_invalidator(
            self._server_id,
            lambda: registry.remove_namespace(namespace),
        )

    def remove_registry_tools(self, registry: ToolRegistry) -> None:
        registry.remove_namespace(f"mcp.{self._server_id}")

    def _registered(
        self,
        compiled: CompiledMcpTool,
        *,
        generation: int,
    ) -> RegisteredTool:
        tool = compiled.tool
        namespace_name = f"mcp.{self._server_id}.{tool.name}"
        schema = cast(dict[str, JsonValue], dict(tool.inputSchema))
        input_model = type(
            f"Mcp_{self._server_id}_{tool.name}_Arguments".replace("-", "_"),
            (_McpArguments,),
            {"schema_validator": compiled.validator},
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
                    generation=generation,
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
                capability="mcp.invoke",
                read_only=False,
                display_metadata={"mcp_annotations": annotations},
            ),
            input_model=input_model,
            handler=handler,
            timeout_resolver=_mcp_executor_timeout,
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


def _mcp_executor_timeout(_: BaseModel) -> float:
    return _MCP_EXECUTOR_TIMEOUT_SECONDS
