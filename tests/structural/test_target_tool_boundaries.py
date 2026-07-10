from __future__ import annotations

import ast
from pathlib import Path

from awesome_agent.core.tools.builtins import register_read_tools
from awesome_agent.core.tools.registry import ToolRegistry

FORBIDDEN_TOOL_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.artifacts",
    "awesome_agent.persistence",
    "awesome_agent.runtime",
    "awesome_agent.sandbox",
    "awesome_agent.surfaces",
    "awesome_agent.tools",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_read_tool_names_and_descriptions_are_exact() -> None:
    registry = ToolRegistry()
    register_read_tools(registry)

    assert [(spec.name, spec.description) for spec in registry.specifications()] == [
        ("glob", "Find files matching a glob pattern"),
        ("grep", "Search file contents"),
        ("ls", "List files in a directory"),
        ("read_file", "Read file contents"),
    ]


def test_target_tools_do_not_import_legacy_execution_layers() -> None:
    tools_root = Path("src/awesome_agent/core/tools")

    violations = {
        path.as_posix(): sorted(
            imported
            for imported in _imports(path)
            if any(
                imported == denied or imported.startswith(f"{denied}.")
                for denied in FORBIDDEN_TOOL_IMPORTS
            )
        )
        for path in tools_root.rglob("*.py")
    }
    assert {path: imports for path, imports in violations.items() if imports} == {}
