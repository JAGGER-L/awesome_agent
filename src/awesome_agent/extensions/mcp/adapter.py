from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterator
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
_CONTENT_HEAD_CHARS = 24_000
_CONTENT_TAIL_CHARS = 5_000
_MAX_CONTENT_BLOCKS = 1_024
_MAX_STRUCTURED_WIRE_BYTES = 64 * 1024
_MAX_STRUCTURED_NODES = 4_096
_MAX_STRUCTURED_DEPTH = 64
_MCP_EXECUTOR_TIMEOUT_SECONDS = 40.0
_UNSAFE_STRUCTURED_OUTPUT_MESSAGE = (
    "MCP tool returned structured output outside safe resource limits."
)
_UNSAFE_CONTENT_OUTPUT_MESSAGE = (
    "MCP tool returned content outside safe resource limits."
)

type _StructuredChild = tuple[str | None, object]
type _StructuredFrame = tuple[Iterator[_StructuredChild], int, int]


class _UnsafeStructuredOutput(ValueError):
    """Structured MCP output cannot be processed within local resource bounds."""


class _UnsafeContentOutput(ValueError):
    """MCP content blocks cannot be processed within local resource bounds."""


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
            try:
                _preflight_content(result)
            except _UnsafeContentOutput as error:
                raise ExpectedToolFailure(
                    ToolErrorCode.EXECUTION_FAILED,
                    _UNSAFE_CONTENT_OUTPUT_MESSAGE,
                    retryable=False,
                ) from error
            try:
                _preflight_structured_content(result.structuredContent)
            except _UnsafeStructuredOutput as error:
                raise ExpectedToolFailure(
                    ToolErrorCode.EXECUTION_FAILED,
                    _UNSAFE_STRUCTURED_OUTPUT_MESSAGE,
                    retryable=False,
                ) from error
            if result.isError is True:
                content, _ = _bounded_content(result)
                raise ExpectedToolFailure(
                    ToolErrorCode.EXECUTION_FAILED,
                    content[:2_000] or "MCP tool reported an error.",
                )
            _validate_structured_output(compiled, result)
            try:
                content, truncated = _bounded_content(result)
            except (TypeError, ValueError) as error:
                raise ExpectedToolFailure(
                    ToolErrorCode.EXECUTION_FAILED,
                    "MCP tool returned content that could not be represented safely.",
                    retryable=False,
                ) from error
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
    builder = _BoundedTextBuilder()
    if result.content:
        for index, item in enumerate(result.content):
            if index:
                builder.add("\n")
            if isinstance(item, TextContent):
                builder.add(item.text)
            else:
                builder.add(f"[MCP content: {item.type}]")
    elif result.structuredContent is not None:
        encoder = json.JSONEncoder(
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        for chunk in encoder.iterencode(result.structuredContent):
            builder.add(chunk)
    return builder.finish()


def _preflight_content(result: CallToolResult) -> None:
    if len(result.content) > _MAX_CONTENT_BLOCKS:
        raise _UnsafeContentOutput


class _BoundedTextBuilder:
    """Retain bounded head/tail windows while accounting for the full output."""

    def __init__(self) -> None:
        self._total_chars = 0
        self._exact_parts: list[str] | None = []
        self._head_parts: list[str] = []
        self._head_chars = 0
        self._tail = ""

    def add(self, chunk: str) -> None:
        if not chunk:
            return
        self._total_chars += len(chunk)
        exact_parts = self._exact_parts
        if exact_parts is not None:
            exact_parts.append(chunk)
            if self._total_chars > _MAX_CONTENT_CHARS:
                self._exact_parts = None

        head_remaining = _CONTENT_HEAD_CHARS - self._head_chars
        if head_remaining > 0:
            head = chunk[:head_remaining]
            self._head_parts.append(head)
            self._head_chars += len(head)

        if len(chunk) >= _CONTENT_TAIL_CHARS:
            self._tail = chunk[-_CONTENT_TAIL_CHARS:]
        else:
            self._tail = (self._tail + chunk)[-_CONTENT_TAIL_CHARS:]

    def finish(self) -> tuple[str, bool]:
        exact_parts = self._exact_parts
        if exact_parts is not None:
            return "".join(exact_parts), False
        retained = _CONTENT_HEAD_CHARS + _CONTENT_TAIL_CHARS
        omitted = self._total_chars - retained
        marker = f"\n...[MCP output truncated: {omitted} characters omitted]...\n"
        return f"{''.join(self._head_parts)}{marker}{self._tail}", True


def _validate_structured_output(
    compiled: CompiledMcpTool,
    result: CallToolResult,
) -> None:
    validator = compiled.output_validator
    if validator is None:
        return
    structured = result.structuredContent
    if structured is None:
        raise ExpectedToolFailure(
            ToolErrorCode.EXECUTION_FAILED,
            "MCP tool omitted output required by its declared schema.",
            retryable=False,
        )
    try:
        validator.validate(cast(JsonValue, structured))
    except JsonSchemaValidationError as error:
        raise ExpectedToolFailure(
            ToolErrorCode.EXECUTION_FAILED,
            "MCP tool returned output that did not match its declared schema.",
            retryable=False,
        ) from error


def _preflight_structured_content(structured: object | None) -> None:
    """Bound JSON work before schema traversal or rendering.

    The walk is iterative and advances container iterators one child at a time,
    so an oversized collection cannot first create an equally oversized work
    stack. The wire estimate matches the UTF-8 JSON representation closely and
    stops scanning strings as soon as the byte budget is exhausted.
    """

    if structured is None:
        return
    if not isinstance(structured, dict):
        raise _UnsafeStructuredOutput

    wire_bytes = 0
    nodes = 0
    active_containers: set[int] = set()
    frames: list[_StructuredFrame] = []
    current: tuple[object, int] | None = (structured, 1)

    try:
        while True:
            if current is not None:
                value, depth = current
                current = None
                nodes += 1
                if nodes > _MAX_STRUCTURED_NODES:
                    raise _UnsafeStructuredOutput
                if depth > _MAX_STRUCTURED_DEPTH:
                    raise _UnsafeStructuredOutput

                if isinstance(value, dict):
                    identity = id(value)
                    if identity in active_containers:
                        raise _UnsafeStructuredOutput
                    active_containers.add(identity)
                    item_count = len(value)
                    wire_bytes = _add_wire_bytes(
                        wire_bytes,
                        2 + max(0, item_count - 1) + item_count,
                    )
                    frames.append((_iter_object_children(value), depth + 1, identity))
                elif isinstance(value, list):
                    identity = id(value)
                    if identity in active_containers:
                        raise _UnsafeStructuredOutput
                    active_containers.add(identity)
                    wire_bytes = _add_wire_bytes(
                        wire_bytes,
                        2 + max(0, len(value) - 1),
                    )
                    frames.append((_iter_array_children(value), depth + 1, identity))
                else:
                    wire_bytes = _add_wire_bytes(
                        wire_bytes,
                        _scalar_wire_bytes(
                            value,
                            remaining=_MAX_STRUCTURED_WIRE_BYTES - wire_bytes,
                        ),
                    )

            while frames and current is None:
                iterator, child_depth, identity = frames[-1]
                try:
                    key, child = next(iterator)
                except StopIteration:
                    frames.pop()
                    active_containers.remove(identity)
                    continue
                if key is not None:
                    wire_bytes = _add_wire_bytes(
                        wire_bytes,
                        _json_string_wire_bytes(
                            key,
                            remaining=_MAX_STRUCTURED_WIRE_BYTES - wire_bytes,
                        ),
                    )
                current = (child, child_depth)

            if current is None:
                return
    except _UnsafeStructuredOutput:
        raise
    except (OverflowError, RuntimeError, TypeError, ValueError) as error:
        raise _UnsafeStructuredOutput from error


def _iter_object_children(value: dict[object, object]) -> Iterator[_StructuredChild]:
    for key, child in value.items():
        if not isinstance(key, str):
            raise _UnsafeStructuredOutput
        yield key, child


def _iter_array_children(value: list[object]) -> Iterator[_StructuredChild]:
    for child in value:
        yield None, child


def _add_wire_bytes(current: int, additional: int) -> int:
    total = current + additional
    if total > _MAX_STRUCTURED_WIRE_BYTES:
        raise _UnsafeStructuredOutput
    return total


def _scalar_wire_bytes(value: object, *, remaining: int) -> int:
    if value is None or value is True:
        return 4
    if value is False:
        return 5
    if isinstance(value, str):
        return _json_string_wire_bytes(value, remaining=remaining)
    if isinstance(value, int):
        if value == 0:
            return 1
        digits = (abs(value).bit_length() * 30_103) // 100_000 + 1
        return digits + (1 if value < 0 else 0)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _UnsafeStructuredOutput
        return len(repr(value))
    raise _UnsafeStructuredOutput


def _json_string_wire_bytes(value: str, *, remaining: int) -> int:
    # Every Unicode code point costs at least one byte plus the surrounding
    # quotes, so this rejects very large strings without walking them.
    if len(value) + 2 > remaining:
        raise _UnsafeStructuredOutput
    size = 2
    for character in value:
        codepoint = ord(character)
        if codepoint <= 0x1F:
            size += 6
        elif character in {'"', "\\"}:
            size += 2
        elif codepoint <= 0x7F:
            size += 1
        elif codepoint <= 0x7FF:
            size += 2
        elif 0xD800 <= codepoint <= 0xDFFF:
            raise _UnsafeStructuredOutput
        elif codepoint <= 0xFFFF:
            size += 3
        else:
            size += 4
        if size > remaining:
            raise _UnsafeStructuredOutput
    return size


def _mcp_executor_timeout(_: BaseModel) -> float:
    return _MCP_EXECUTOR_TIMEOUT_SECONDS
