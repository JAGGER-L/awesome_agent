from __future__ import annotations

import ast
from pathlib import Path

LEGACY_EXTENSION_FILES = {
    "assembly.py",
    "catalog.py",
    "catalog_store.py",
    "community.py",
    "config.py",
    "diagnostics.py",
    "hooks.py",
    "models.py",
    "runtime_catalog.py",
    "service.py",
    "sources.py",
}

FORBIDDEN_EXTENSION_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.persistence",
    "awesome_agent.runtime",
    "awesome_agent.surfaces",
    "awesome_agent.tools",
    "awesome_agent.tui",
    "fastapi",
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


def test_only_target_skill_and_stdio_mcp_packages_remain() -> None:
    root = Path("src/awesome_agent/extensions")

    assert not {path.name for path in root.iterdir()} & LEGACY_EXTENSION_FILES
    assert not (root / "mcp" / "http.py").exists()
    assert (root / "skills").is_dir()
    assert (root / "mcp" / "stdio.py").is_file()


def test_mcp_uses_sdk_without_handwritten_protocol_or_extra_surfaces() -> None:
    root = Path("src/awesome_agent/extensions/mcp")
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in root.rglob("*.py")
    ).casefold()

    assert "jsonrpc" not in source
    assert "tools/list" not in source
    assert "tools/call" not in source
    assert "streamable_http" not in source
    assert "oauth" not in source
    assert "list_resources" not in source
    assert "list_prompts" not in source
    assert "mcp.client.stdio" in source


def test_skill_models_have_no_legacy_capability_or_team_contracts() -> None:
    models = Path("src/awesome_agent/extensions/skills/models.py").read_text(
        encoding="utf-8"
    )

    assert "requested_tools" not in models
    assert "required_capabilities" not in models
    assert "compatible_routes" not in models
    assert "actor_kinds" not in models
    assert "team" not in models.casefold()


def test_extensions_do_not_import_legacy_runtime_persistence_or_ui() -> None:
    root = Path("src/awesome_agent/extensions")
    violations = {
        path.as_posix(): sorted(
            imported
            for imported in _imports(path)
            if any(
                imported == denied or imported.startswith(f"{denied}.")
                for denied in FORBIDDEN_EXTENSION_IMPORTS
            )
        )
        for path in root.rglob("*.py")
    }

    assert {path: imports for path, imports in violations.items() if imports} == {}


def test_mcp_tool_calls_exist_only_inside_shared_executor_adapter() -> None:
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
