from __future__ import annotations

import ast
from pathlib import Path

from awesome_agent.extensions.skills.models import SkillDescriptor

EXTENSION_ENTRIES = {"__init__.py", "mcp", "skills"}
MCP_MODULES = {"__init__.py", "adapter.py", "manager.py", "models.py", "stdio.py"}
SKILL_MODEL_FIELDS = {
    "allowed_tools",
    "compatibility",
    "description",
    "license",
    "metadata",
    "name",
    "root",
    "source",
}


def test_extension_inventory_is_current() -> None:
    root = Path("src/awesome_agent/extensions")
    entries = {path.name for path in root.iterdir() if path.name != "__pycache__"}
    mcp_modules = {path.name for path in (root / "mcp").glob("*.py")}

    assert entries == EXTENSION_ENTRIES
    assert mcp_modules == MCP_MODULES


def test_mcp_uses_the_sdk_stdio_client() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/awesome_agent/extensions/mcp").glob("*.py")
    )

    assert "mcp.client.stdio" in source


def test_skill_descriptor_is_the_manifest_contract() -> None:
    assert set(SkillDescriptor.model_fields) == SKILL_MODEL_FIELDS


def test_mcp_tool_calls_flow_through_the_shared_adapter() -> None:
    application_and_graph = (
        *Path("src/awesome_agent/application").rglob("*.py"),
        *Path("src/awesome_agent/agent").rglob("*.py"),
    )
    direct_calls: list[str] = []
    for path in application_and_graph:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Attribute) and node.attr == "call_tool"
            for node in ast.walk(tree)
        ):
            direct_calls.append(path.as_posix())

    adapter = Path("src/awesome_agent/extensions/mcp/adapter.py").read_text(
        encoding="utf-8"
    )
    assert direct_calls == []
    assert "RegisteredTool" in adapter
    assert "replace_namespace" in adapter
    assert "ExpectedToolFailure" in adapter
