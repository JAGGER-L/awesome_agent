from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_CONTEXT_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.application",
    "awesome_agent.artifacts",
    "awesome_agent.attachments",
    "awesome_agent.persistence",
    "awesome_agent.providers",
    "awesome_agent.runtime",
    "awesome_agent.surfaces",
    "awesome_agent.tui",
    "fastapi",
    "httpx",
    "openai",
    "requests",
    "sqlalchemy",
    "textual",
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


def test_context_package_has_no_legacy_surface_or_network_dependencies() -> None:
    root = Path("src/awesome_agent/context")
    violations = {
        path.as_posix(): sorted(
            imported
            for imported in _imports(path)
            if any(
                imported == denied or imported.startswith(f"{denied}.")
                for denied in FORBIDDEN_CONTEXT_IMPORTS
            )
        )
        for path in root.rglob("*.py")
    }

    assert {path: imports for path, imports in violations.items() if imports} == {}


def test_agent_nodes_invoke_context_explicitly_without_middleware() -> None:
    nodes = Path("src/awesome_agent/agent/nodes.py").read_text(encoding="utf-8")
    context_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/awesome_agent/context").rglob("*.py")
    )

    assert "context.context_builder(state)" in nodes
    assert "context.compressor.compress(updated)" in nodes
    assert "Middleware" not in context_sources
    assert "middleware" not in context_sources.casefold()
