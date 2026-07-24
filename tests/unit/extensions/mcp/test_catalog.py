from __future__ import annotations

from collections.abc import Mapping

import pytest
from jsonschema.exceptions import ValidationError
from mcp.types import Tool

from awesome_agent.extensions.mcp.catalog import (
    MAX_MCP_CATALOG_BYTES,
    MAX_MCP_SCHEMA_BYTES,
    MAX_MCP_SCHEMA_DEPTH,
    MAX_MCP_TOOLS,
    McpCatalogError,
    compile_mcp_catalog,
)


def tool(name: str, schema: Mapping[str, object]) -> Tool:
    return Tool(name=name, description=f"Tool {name}", inputSchema=dict(schema))


def test_catalog_compiles_full_json_schema_and_local_references() -> None:
    catalog = compile_mcp_catalog(
        (
            tool(
                "search",
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$defs": {
                        "positive": {"type": "integer", "minimum": 1},
                    },
                    "type": "object",
                    "properties": {
                        "query": {
                            "oneOf": [
                                {"type": "string", "minLength": 3},
                                {"$ref": "#/$defs/positive"},
                            ]
                        },
                        "mode": {"enum": ["fast", "complete"]},
                    },
                    "required": ["query"],
                    "unevaluatedProperties": False,
                },
            ),
        ),
        generation=7,
    )

    compiled = catalog.resolve("search")
    assert catalog.generation == 7
    compiled.validator.validate({"query": "hello", "mode": "fast"})
    compiled.validator.validate({"query": 2})
    with pytest.raises(ValidationError):
        compiled.validator.validate({"query": "x"})
    with pytest.raises(ValidationError):
        compiled.validator.validate({"query": 0})
    with pytest.raises(ValidationError):
        compiled.validator.validate({"query": "hello", "extra": True})


def test_catalog_keeps_format_as_annotation_by_default() -> None:
    catalog = compile_mcp_catalog(
        (tool("email", {"type": "string", "format": "email"}),)
    )

    catalog.resolve("email").validator.validate("not-an-email")


def test_catalog_does_not_treat_annotation_payloads_as_schema_references() -> None:
    catalog = compile_mcp_catalog(
        (
            tool(
                "annotated",
                {
                    "type": "object",
                    "examples": [{"$ref": "https://example.invalid/annotation-only"}],
                    "properties": {
                        "payload": {
                            "default": {"$ref": "https://example.invalid/default-only"}
                        }
                    },
                },
            ),
        )
    )

    catalog.resolve("annotated").validator.validate({})


def test_catalog_preflights_schema_reached_through_local_pointer() -> None:
    schema = {
        "examples": [{"$ref": "https://example.invalid/remote"}],
        "$ref": "#/examples/0",
    }

    with pytest.raises(McpCatalogError, match="external"):
        compile_mcp_catalog((tool("reachable_annotation", schema),))


def test_catalog_validates_schema_reached_through_local_pointer() -> None:
    schema = {
        "examples": [{"type": 123}],
        "$ref": "#/examples/0",
    }

    with pytest.raises(McpCatalogError, match="invalid JSON Schema"):
        compile_mcp_catalog((tool("invalid_reachable_annotation", schema),))


@pytest.mark.parametrize(
    ("reference", "reason"),
    [
        ("https://example.invalid/schema.json", "external"),
        ("other.json#/$defs/value", "external"),
        ("#/$defs/missing", "missing"),
        ("#missing-anchor", "missing"),
    ],
)
def test_catalog_rejects_external_and_missing_references(
    reference: str,
    reason: str,
) -> None:
    schema = {
        "$defs": {"present": {"type": "string"}},
        "$ref": reference,
    }

    with pytest.raises(McpCatalogError, match=reason):
        compile_mcp_catalog((tool("unsafe", schema),))


def test_catalog_rejects_unknown_dialect_and_required_vocabulary() -> None:
    with pytest.raises(McpCatalogError, match="dialect"):
        compile_mcp_catalog(
            (
                tool(
                    "unknown",
                    {"$schema": "https://example.invalid/draft/schema"},
                ),
            )
        )

    with pytest.raises(McpCatalogError, match="vocabulary"):
        compile_mcp_catalog(
            (
                tool(
                    "unknown_vocab",
                    {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "$vocabulary": {
                            "https://example.invalid/vocab": True,
                        },
                    },
                ),
            )
        )


def test_catalog_rejects_nested_unknown_dialect_and_required_vocabulary() -> None:
    with pytest.raises(McpCatalogError, match="dialect"):
        compile_mcp_catalog(
            (
                tool(
                    "nested_dialect",
                    {
                        "$defs": {
                            "child": {
                                "$id": "child",
                                "$schema": "https://example.invalid/draft/schema",
                            }
                        }
                    },
                ),
            )
        )

    with pytest.raises(McpCatalogError, match="vocabulary"):
        compile_mcp_catalog(
            (
                tool(
                    "nested_vocab",
                    {
                        "$defs": {
                            "child": {
                                "$id": "child",
                                "$schema": (
                                    "https://json-schema.org/draft/2020-12/schema"
                                ),
                                "$vocabulary": {
                                    "https://example.invalid/vocab": True,
                                },
                            }
                        }
                    },
                ),
            )
        )


def test_catalog_preflights_draft3_schema_locations_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls: list[str] = []

    def fail_network(*args: object, **kwargs: object) -> object:
        del args, kwargs
        network_calls.append("called")
        raise AssertionError("schema compilation attempted network access")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    with pytest.raises(McpCatalogError, match="external"):
        compile_mcp_catalog(
            (
                tool(
                    "legacy",
                    {
                        "$schema": "http://json-schema.org/draft-03/schema#",
                        "extends": {
                            "$ref": "https://example.invalid/remote-schema",
                        },
                    },
                ),
            )
        )
    assert network_calls == []

    catalog = compile_mcp_catalog(
        (
            tool(
                "legacy_local",
                {
                    "$schema": "http://json-schema.org/draft-03/schema#",
                    "properties": {"base": {"type": "string"}},
                    "extends": {"$ref": "#/properties/base"},
                },
            ),
        )
    )
    catalog.resolve("legacy_local").validator.validate("valid")
    with pytest.raises(ValidationError):
        catalog.resolve("legacy_local").validator.validate(1)


def test_catalog_preflights_draft2019_tuple_items() -> None:
    with pytest.raises(McpCatalogError, match="external"):
        compile_mcp_catalog(
            (
                tool(
                    "tuple_items",
                    {
                        "$schema": "https://json-schema.org/draft/2019-09/schema",
                        "items": [{"$ref": "https://example.invalid/remote-schema"}],
                    },
                ),
            )
        )


def test_catalog_resolves_fragments_within_the_current_schema_resource() -> None:
    valid = {
        "$id": "https://example.invalid/root",
        "$defs": {
            "child": {
                "$id": "child",
                "$anchor": "local",
                "type": "object",
                "properties": {"value": {"$ref": "#local"}},
            }
        },
    }
    compile_mcp_catalog((tool("valid_resource", valid),))

    wrong_resource = {
        "$id": "https://example.invalid/root",
        "$defs": {
            "anchor_owner": {
                "$id": "owner",
                "$anchor": "local",
                "type": "string",
            },
            "ref_owner": {
                "$id": "other",
                "$ref": "#local",
            },
        },
    }
    with pytest.raises(McpCatalogError, match="missing"):
        compile_mcp_catalog((tool("wrong_resource", wrong_resource),))


def test_catalog_rejects_duplicate_invalid_and_oversized_contracts() -> None:
    with pytest.raises(McpCatalogError, match="duplicate"):
        compile_mcp_catalog((tool("echo", {}), tool("echo", {})))
    with pytest.raises(McpCatalogError, match="name"):
        compile_mcp_catalog((tool("Not.Valid", {}),))
    with pytest.raises(McpCatalogError, match="128"):
        compile_mcp_catalog(
            tuple(tool(f"tool_{index}", {}) for index in range(MAX_MCP_TOOLS + 1))
        )

    invalid_output = Tool(
        name="invalid_output",
        description="invalid output",
        inputSchema={},
        outputSchema={"$ref": "https://example.invalid/output"},
    )
    with pytest.raises(McpCatalogError, match="external"):
        compile_mcp_catalog((invalid_output,))

    oversized_contract = Tool(
        name="oversized_contract",
        description="oversized contract",
        inputSchema={},
        _meta={"payload": "x" * MAX_MCP_CATALOG_BYTES},
    )
    with pytest.raises(McpCatalogError, match="catalog"):
        compile_mcp_catalog((oversized_contract,))

    oversized = {"type": "string", "default": "x" * MAX_MCP_SCHEMA_BYTES}
    with pytest.raises(McpCatalogError, match="256"):
        compile_mcp_catalog((tool("oversized", oversized),))

    per_tool_size = MAX_MCP_CATALOG_BYTES // 5
    with pytest.raises(McpCatalogError, match="catalog"):
        compile_mcp_catalog(
            tuple(
                tool(
                    f"large_{index}",
                    {"type": "string", "default": "x" * per_tool_size},
                )
                for index in range(5)
            )
        )


def test_catalog_rejects_excessive_schema_depth() -> None:
    schema: dict[str, object] = {"type": "string"}
    for _ in range(MAX_MCP_SCHEMA_DEPTH):
        schema = {"allOf": [schema]}

    with pytest.raises(McpCatalogError, match="depth"):
        compile_mcp_catalog((tool("deep", schema),))
