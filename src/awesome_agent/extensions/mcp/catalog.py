from __future__ import annotations

import json
import re
import warnings
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast
from urllib.parse import unquote, urldefrag, urljoin

from jsonschema import (
    Draft3Validator,
    Draft4Validator,
    Draft6Validator,
    Draft7Validator,
    Draft201909Validator,
    Draft202012Validator,
)
from jsonschema.exceptions import SchemaError
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for
from mcp.types import Tool

MAX_MCP_TOOLS = 128
MAX_MCP_SCHEMA_BYTES = 256 * 1024
MAX_MCP_CATALOG_BYTES = 1024 * 1024
MAX_MCP_SCHEMA_DEPTH = 64

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_JSON_POINTER_ARRAY_INDEX = re.compile(r"^(?:0|[1-9][0-9]*)$")
_REFERENCE_KEYWORDS = frozenset({"$ref", "$dynamicRef", "$recursiveRef"})
_ROOT_RESOURCE_URI = "https://awesome-agent.invalid/mcp/catalog/root"


@dataclass(frozen=True, slots=True)
class _DialectSchemaLocations:
    single: frozenset[str] = frozenset()
    single_or_array: frozenset[str] = frozenset()
    arrays: frozenset[str] = frozenset()
    mappings: frozenset[str] = frozenset()
    dependency_mappings: frozenset[str] = frozenset()


_COMMON_COMPOSITION_ARRAYS = frozenset({"allOf", "anyOf", "oneOf"})
_COMMON_OBJECT_MAPPINGS = frozenset({"patternProperties", "properties"})
_DIALECT_SCHEMA_LOCATIONS: dict[type[Validator], _DialectSchemaLocations] = {
    Draft3Validator: _DialectSchemaLocations(
        single=frozenset({"additionalItems", "additionalProperties"}),
        single_or_array=frozenset({"disallow", "extends", "items", "type"}),
        mappings=_COMMON_OBJECT_MAPPINGS,
        dependency_mappings=frozenset({"dependencies"}),
    ),
    Draft4Validator: _DialectSchemaLocations(
        single=frozenset({"additionalItems", "additionalProperties", "not"}),
        single_or_array=frozenset({"items"}),
        arrays=_COMMON_COMPOSITION_ARRAYS,
        mappings=frozenset({"definitions", *_COMMON_OBJECT_MAPPINGS}),
        dependency_mappings=frozenset({"dependencies"}),
    ),
    Draft6Validator: _DialectSchemaLocations(
        single=frozenset(
            {
                "additionalItems",
                "additionalProperties",
                "contains",
                "not",
                "propertyNames",
            }
        ),
        single_or_array=frozenset({"items"}),
        arrays=_COMMON_COMPOSITION_ARRAYS,
        mappings=frozenset({"definitions", *_COMMON_OBJECT_MAPPINGS}),
        dependency_mappings=frozenset({"dependencies"}),
    ),
    Draft7Validator: _DialectSchemaLocations(
        single=frozenset(
            {
                "additionalItems",
                "additionalProperties",
                "contains",
                "else",
                "if",
                "not",
                "propertyNames",
                "then",
            }
        ),
        single_or_array=frozenset({"items"}),
        arrays=_COMMON_COMPOSITION_ARRAYS,
        mappings=frozenset({"definitions", *_COMMON_OBJECT_MAPPINGS}),
        dependency_mappings=frozenset({"dependencies"}),
    ),
    Draft201909Validator: _DialectSchemaLocations(
        single=frozenset(
            {
                "additionalItems",
                "additionalProperties",
                "contains",
                "contentSchema",
                "else",
                "if",
                "not",
                "propertyNames",
                "then",
                "unevaluatedItems",
                "unevaluatedProperties",
            }
        ),
        single_or_array=frozenset({"items"}),
        arrays=_COMMON_COMPOSITION_ARRAYS,
        mappings=frozenset(
            {"$defs", "definitions", "dependentSchemas", *_COMMON_OBJECT_MAPPINGS}
        ),
    ),
    Draft202012Validator: _DialectSchemaLocations(
        single=frozenset(
            {
                "additionalProperties",
                "contains",
                "contentSchema",
                "else",
                "if",
                "items",
                "not",
                "propertyNames",
                "then",
                "unevaluatedItems",
                "unevaluatedProperties",
            }
        ),
        arrays=frozenset({*_COMMON_COMPOSITION_ARRAYS, "prefixItems"}),
        mappings=frozenset({"$defs", "dependentSchemas", *_COMMON_OBJECT_MAPPINGS}),
    ),
}


class McpCatalogError(ValueError):
    """A remote MCP tool catalog cannot be represented safely."""


@dataclass(frozen=True, slots=True)
class CompiledMcpTool:
    tool: Tool
    validator: Validator
    output_validator: Validator | None


@dataclass(frozen=True, slots=True)
class McpCatalog:
    generation: int
    compiled_tools: tuple[CompiledMcpTool, ...]
    _by_name: MappingProxyType[str, CompiledMcpTool]

    @property
    def tools(self) -> tuple[Tool, ...]:
        return tuple(item.tool for item in self.compiled_tools)

    def resolve(self, tool_name: str) -> CompiledMcpTool:
        try:
            return self._by_name[tool_name]
        except KeyError as error:
            raise McpCatalogError("MCP tool is not present in the catalog") from error


def compile_mcp_catalog(
    tools: tuple[Tool, ...],
    *,
    generation: int = 0,
) -> McpCatalog:
    """Compile and fully check one remote catalog without external I/O."""

    if generation < 0:
        raise ValueError("MCP catalog generation cannot be negative")
    if len(tools) > MAX_MCP_TOOLS:
        raise McpCatalogError(f"MCP catalog exceeds the {MAX_MCP_TOOLS}-tool limit")

    compiled: list[CompiledMcpTool] = []
    names: set[str] = set()
    catalog_bytes = 0
    for tool in tools:
        _validate_tool_contract(tool, names)
        catalog_bytes += mcp_tool_contract_size(tool)
        if catalog_bytes > MAX_MCP_CATALOG_BYTES:
            raise McpCatalogError("MCP catalog exceeds the 1 MiB limit")
        schema = cast(dict[str, object], dict(tool.inputSchema))
        validator = _compile_schema(schema)
        output_validator = (
            None
            if tool.outputSchema is None
            else _compile_schema(cast(dict[str, object], dict(tool.outputSchema)))
        )
        compiled.append(
            CompiledMcpTool(
                tool=tool,
                validator=validator,
                output_validator=output_validator,
            )
        )

    by_name = MappingProxyType({item.tool.name: item for item in compiled})
    return McpCatalog(
        generation=generation,
        compiled_tools=tuple(compiled),
        _by_name=by_name,
    )


def mcp_tool_contract_size(tool: Tool) -> int:
    """Measure one wire catalog entry using the compiler's canonical encoding."""

    return _serialized_size(tool.model_dump(by_alias=True, exclude_none=True))


def _validate_tool_contract(tool: Tool, names: set[str]) -> None:
    if _TOOL_NAME.fullmatch(tool.name) is None:
        raise McpCatalogError("MCP tool name is not supported")
    if tool.name in names:
        raise McpCatalogError("MCP catalog contains a duplicate tool name")
    names.add(tool.name)
    if tool.description is not None and len(tool.description) > 500:
        raise McpCatalogError("MCP tool description exceeds the 500-character limit")


def _serialized_size(value: object) -> int:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise McpCatalogError("MCP tool schema is not bounded JSON") from error
    return len(serialized.encode("utf-8"))


def _compile_schema(schema: dict[str, object]) -> Validator:
    if _serialized_size(schema) > MAX_MCP_SCHEMA_BYTES:
        raise McpCatalogError("MCP tool schema exceeds the 256 KiB limit")
    validator_type = _validator_type(schema)
    _validate_required_vocabularies(schema, validator_type)
    try:
        validator_type.check_schema(schema)
    except SchemaError as error:
        raise McpCatalogError("MCP tool contains an invalid JSON Schema") from error
    _validate_structure_and_references(schema, validator_type)
    # jsonschema's default registry never retrieves arbitrary remote resources.
    # The preflight above rejects every non-fragment reference first.
    return validator_type(schema)


def _validator_type(schema: dict[str, object]) -> type[Validator]:
    dialect = schema.get("$schema")
    if dialect is None:
        return Draft202012Validator
    if not isinstance(dialect, str):
        raise McpCatalogError("MCP JSON Schema dialect must be a string")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        selected = validator_for(schema)
    if any(item.category is DeprecationWarning for item in caught):
        raise McpCatalogError("MCP JSON Schema dialect is not supported")
    if selected not in _DIALECT_SCHEMA_LOCATIONS:
        raise McpCatalogError("MCP JSON Schema dialect is not supported")
    return selected


def _validate_required_vocabularies(
    schema: dict[str, object],
    validator_type: type[Validator],
) -> None:
    vocabularies = schema.get("$vocabulary")
    if vocabularies is None:
        return
    if not isinstance(vocabularies, dict):
        raise McpCatalogError("MCP JSON Schema vocabulary declaration is invalid")
    supported_value = validator_type.META_SCHEMA.get("$vocabulary", {})
    supported = supported_value if isinstance(supported_value, dict) else {}
    for uri, required in vocabularies.items():
        if not isinstance(uri, str) or not isinstance(required, bool):
            raise McpCatalogError("MCP JSON Schema vocabulary declaration is invalid")
        if required and uri not in supported:
            raise McpCatalogError("MCP JSON Schema requires an unknown vocabulary")


def _validate_structure_and_references(
    schema: dict[str, object],
    validator_type: type[Validator],
) -> None:
    root_identifier = _schema_identifier(schema, validator_type)
    root_uri, root_fragment = _resolve_resource_identifier(
        _ROOT_RESOURCE_URI,
        root_identifier,
    )
    resource_roots: dict[str, dict[str, object] | bool] = {root_uri: schema}
    anchors: dict[str, set[str]] = {root_uri: set()}
    if root_fragment:
        anchors[root_uri].add(root_fragment)
    pointer_references: list[
        tuple[
            str,
            str,
            dict[str, object] | bool,
            type[Validator],
            int,
        ]
    ] = []
    anchor_references: list[tuple[str, str, dict[str, object] | bool]] = []
    stack: list[
        tuple[
            dict[str, object] | bool,
            int,
            str,
            dict[str, object] | bool,
            type[Validator],
        ]
    ] = [(schema, 1, root_uri, schema, validator_type)]
    visited: set[int] = set()
    while stack or pointer_references:
        while stack:
            value, depth, resource_uri, resource_root, inherited_validator = stack.pop()
            if depth > MAX_MCP_SCHEMA_DEPTH:
                raise McpCatalogError(
                    f"MCP tool schema exceeds the depth limit of {MAX_MCP_SCHEMA_DEPTH}"
                )
            if isinstance(value, bool):
                continue
            identity = id(value)
            if identity in visited:
                continue
            visited.add(identity)
            current_validator = inherited_validator
            if "$schema" in value:
                current_validator = _validator_type(value)
                _validate_required_vocabularies(value, current_validator)
                if value is not schema:
                    try:
                        current_validator.check_schema(value)
                    except SchemaError as error:
                        raise McpCatalogError(
                            "MCP tool contains an invalid JSON Schema"
                        ) from error
            elif "$vocabulary" in value:
                _validate_required_vocabularies(value, current_validator)
            for anchor_keyword in ("$anchor", "$dynamicAnchor"):
                anchor = value.get(anchor_keyword)
                if isinstance(anchor, str):
                    resource_anchors = anchors.setdefault(resource_uri, set())
                    if anchor in resource_anchors:
                        raise McpCatalogError(
                            "MCP JSON Schema contains a duplicate anchor"
                        )
                    resource_anchors.add(anchor)
            for key in _REFERENCE_KEYWORDS:
                reference = value.get(key)
                if reference is None:
                    continue
                if not isinstance(reference, str):
                    raise McpCatalogError("MCP JSON Schema reference is invalid")
                if not reference.startswith("#"):
                    raise McpCatalogError(
                        "MCP JSON Schema external references are forbidden"
                    )
                fragment = unquote(reference[1:])
                if not fragment or fragment.startswith("/"):
                    pointer_references.append(
                        (
                            reference,
                            resource_uri,
                            resource_root,
                            current_validator,
                            depth + 1,
                        )
                    )
                else:
                    anchor_references.append((reference, resource_uri, resource_root))
            for child in _schema_children(value, current_validator):
                child_uri = resource_uri
                child_root = resource_root
                child_validator = current_validator
                if isinstance(child, dict):
                    if "$schema" in child:
                        child_validator = _validator_type(child)
                    identifier = _schema_identifier(child, child_validator)
                    if identifier is not None:
                        child_uri, fragment = _resolve_resource_identifier(
                            resource_uri,
                            identifier,
                        )
                        if child_uri != resource_uri:
                            existing = resource_roots.get(child_uri)
                            if existing is not None and existing is not child:
                                raise McpCatalogError(
                                    "MCP JSON Schema contains a duplicate resource "
                                    "identifier"
                                )
                            resource_roots[child_uri] = child
                            child_root = child
                        if fragment:
                            resource_anchors = anchors.setdefault(child_uri, set())
                            if fragment in resource_anchors:
                                raise McpCatalogError(
                                    "MCP JSON Schema contains a duplicate anchor"
                                )
                            resource_anchors.add(fragment)
                stack.append((child, depth + 1, child_uri, child_root, child_validator))

        if not pointer_references:
            break
        reference, resource_uri, resource_root, reference_validator, depth = (
            pointer_references.pop()
        )
        target = _validate_local_reference(
            resource_root,
            reference,
            anchors.get(resource_uri, set()),
        )
        if target is not None and id(target) not in visited:
            target_uri = resource_uri
            target_root = resource_root
            target_validator = reference_validator
            if isinstance(target, dict):
                if "$schema" in target:
                    target_validator = _validator_type(target)
                _validate_required_vocabularies(target, target_validator)
                try:
                    target_validator.check_schema(target)
                except SchemaError as error:
                    raise McpCatalogError(
                        "MCP tool contains an invalid JSON Schema"
                    ) from error
                identifier = _schema_identifier(target, target_validator)
                if identifier is not None:
                    target_uri, fragment = _resolve_resource_identifier(
                        resource_uri,
                        identifier,
                    )
                    if target_uri != resource_uri:
                        existing = resource_roots.get(target_uri)
                        if existing is not None and existing is not target:
                            raise McpCatalogError(
                                "MCP JSON Schema contains a duplicate resource "
                                "identifier"
                            )
                        resource_roots[target_uri] = target
                        target_root = target
                    if fragment:
                        resource_anchors = anchors.setdefault(target_uri, set())
                        if fragment in resource_anchors:
                            raise McpCatalogError(
                                "MCP JSON Schema contains a duplicate anchor"
                            )
                        resource_anchors.add(fragment)
            stack.append((target, depth, target_uri, target_root, target_validator))

    for reference, resource_uri, resource_root in anchor_references:
        _validate_local_reference(
            resource_root,
            reference,
            anchors.get(resource_uri, set()),
        )


def _resolve_resource_identifier(
    base_uri: str,
    identifier: str | None,
) -> tuple[str, str]:
    if identifier is None:
        return base_uri, ""
    if not isinstance(identifier, str) or any(
        character.isspace() for character in identifier
    ):
        raise McpCatalogError("MCP JSON Schema resource identifier is invalid")
    resource_uri, fragment = urldefrag(urljoin(base_uri, identifier))
    return resource_uri or base_uri, unquote(fragment)


def _schema_identifier(
    schema: dict[str, object],
    validator_type: type[Validator],
) -> str | None:
    keyword = "id" if "id" in validator_type.META_SCHEMA else "$id"
    identifier = schema.get(keyword)
    return identifier if isinstance(identifier, str) else None


def _schema_children(
    schema: dict[str, object],
    validator_type: type[Validator],
) -> tuple[dict[str, object] | bool, ...]:
    try:
        locations = _DIALECT_SCHEMA_LOCATIONS[validator_type]
    except KeyError as error:
        raise McpCatalogError("MCP JSON Schema dialect is not supported") from error
    children: list[dict[str, object] | bool] = []
    for keyword in locations.single:
        value = schema.get(keyword)
        if isinstance(value, dict | bool):
            children.append(value)
    for keyword in locations.single_or_array:
        value = schema.get(keyword)
        if isinstance(value, dict | bool):
            children.append(value)
        elif isinstance(value, list):
            children.extend(item for item in value if isinstance(item, dict | bool))
    for keyword in locations.arrays:
        value = schema.get(keyword)
        if isinstance(value, list):
            children.extend(item for item in value if isinstance(item, dict | bool))
    for keyword in locations.mappings:
        value = schema.get(keyword)
        if isinstance(value, dict):
            children.extend(
                item for item in value.values() if isinstance(item, dict | bool)
            )
    for keyword in locations.dependency_mappings:
        dependencies = schema.get(keyword)
        if isinstance(dependencies, dict):
            children.extend(
                item for item in dependencies.values() if isinstance(item, dict | bool)
            )
    return tuple(children)


def _validate_local_reference(
    schema: dict[str, object] | bool,
    reference: str,
    anchors: set[str],
) -> dict[str, object] | bool | None:
    if not reference.startswith("#"):
        raise McpCatalogError("MCP JSON Schema external references are forbidden")
    fragment = unquote(reference[1:])
    if not fragment:
        return schema
    if not fragment.startswith("/"):
        if fragment not in anchors:
            raise McpCatalogError(
                "MCP JSON Schema reference points to a missing anchor"
            )
        return None

    if isinstance(schema, bool):
        raise McpCatalogError("MCP JSON Schema reference points to a missing value")

    current: object = schema
    for encoded_part in fragment[1:].split("/"):
        part = _decode_json_pointer_token(encoded_part)
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        if (
            isinstance(current, list)
            and _JSON_POINTER_ARRAY_INDEX.fullmatch(part) is not None
        ):
            try:
                index = int(part)
            except ValueError:
                index = len(current)
            if index < len(current):
                current = current[index]
                continue
        raise McpCatalogError("MCP JSON Schema reference points to a missing value")
    if not isinstance(current, dict | bool):
        raise McpCatalogError("MCP JSON Schema reference target is not a schema")
    return current


def _decode_json_pointer_token(encoded: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(encoded):
        character = encoded[index]
        if character != "~":
            decoded.append(character)
            index += 1
            continue
        if index + 1 >= len(encoded) or encoded[index + 1] not in {"0", "1"}:
            raise McpCatalogError(
                "MCP JSON Schema reference contains an invalid JSON Pointer token"
            )
        decoded.append("~" if encoded[index + 1] == "0" else "/")
        index += 2
    return "".join(decoded)
