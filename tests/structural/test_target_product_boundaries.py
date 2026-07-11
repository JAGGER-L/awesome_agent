from __future__ import annotations

import ast
import json
from pathlib import Path

from awesome_agent.application.commands import COMMAND_OWNERS, CommandName
from awesome_agent.version import PRODUCT_VERSION

ROOT = Path("src/awesome_agent")
TUI_ROOT = Path("tui")
ENTRYPOINTS = (
    ROOT / "application" / "composition.py",
    ROOT / "protocol" / "stdio.py",
)
FORBIDDEN_INTERNAL = {
    "awesome_agent.runtime",
    "awesome_agent.persistence",
    "awesome_agent.api",
    "awesome_agent.client",
    "awesome_agent.surfaces",
    "awesome_agent.tui",
    "awesome_agent.sandbox",
    "awesome_agent.artifacts",
    "awesome_agent.attachments",
    "awesome_agent.repositories",
}
FORBIDDEN_EXTERNAL = {
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "asyncpg",
    "psycopg",
    "docker",
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


def _module_path(module: str) -> Path | None:
    if not module.startswith("awesome_agent"):
        return None
    relative = module.split(".")[1:]
    module_file = ROOT.joinpath(*relative).with_suffix(".py")
    if module_file.is_file():
        return module_file
    package_file = ROOT.joinpath(*relative, "__init__.py")
    return package_file if package_file.is_file() else None


def test_target_host_recursive_import_graph_excludes_legacy_platform() -> None:
    pending = list(ENTRYPOINTS)
    visited: set[Path] = set()
    violations: dict[str, list[str]] = {}
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        imports = _imports(path)
        denied = sorted(
            imported
            for imported in imports
            if any(
                imported == prefix or imported.startswith(f"{prefix}.")
                for prefix in FORBIDDEN_INTERNAL | FORBIDDEN_EXTERNAL
            )
        )
        if denied:
            violations[path.as_posix()] = denied
        for imported in imports:
            resolved = _module_path(imported)
            if resolved is not None:
                pending.append(resolved)

    assert not violations
    assert len(visited) > 20


def test_concrete_external_adapters_are_wired_only_at_composition_boundary() -> None:
    application_importers = {
        path.name
        for path in (ROOT / "application").glob("*.py")
        if any(
            imported == "awesome_agent.providers"
            or imported.startswith("awesome_agent.providers.")
            for imported in _imports(path)
        )
    }
    protocol_source = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "protocol").glob("*.py")
    )

    assert application_importers == {"composition.py"}
    assert "awesome_agent.providers" not in protocol_source
    assert "awesome_agent.tui" not in protocol_source
    assert "textual" not in protocol_source.casefold()


def test_final_command_inventory_and_absent_commands_are_frozen() -> None:
    assert set(COMMAND_OWNERS) == set(CommandName)
    assert {name.value for name in CommandName} == {
        "new",
        "resume",
        "context",
        "compact",
        "model",
        "thinking",
        "workspace",
        "diff",
        "undo",
        "redo",
        "tools",
        "skills",
        "skill",
        "mcp",
        "memory",
        "status",
        "usage",
        "doctor",
        "config",
        "init",
        "review",
        "debug",
        "test",
        "commit",
        "help",
        "theme",
        "copy",
        "quit",
    }
    assert not {
        "mode",
        "history",
        "threads",
        "attach",
        "clear",
        "permissions",
        "sandbox",
        "api",
        "agent",
        "team",
        "details",
        "editor",
    } & {name.value for name in CommandName}


def test_tui_is_one_minimal_node_22_package() -> None:
    package = json.loads((TUI_ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["name"] == "@awesome-agent/tui"
    assert package["version"] == PRODUCT_VERSION
    assert package["type"] == "module"
    assert package["engines"] == {"node": ">=22"}
    assert "workspaces" not in package
    assert (TUI_ROOT / "package-lock.json").is_file()
    dependencies = set(package.get("dependencies", {}))
    assert not dependencies & {
        "@langchain/langgraph",
        "@reduxjs/toolkit",
        "axios",
        "redux",
        "rxjs",
        "xstate",
        "zustand",
    }
    assert set(package["scripts"]) >= {
        "build",
        "format:check",
        "lint",
        "test",
        "typecheck",
    }
