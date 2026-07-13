from __future__ import annotations

import ast
import json
import re
import tomllib
from importlib.metadata import version as distribution_version
from pathlib import Path

from pytest import MonkeyPatch

from awesome_agent.application.commands import COMMAND_OWNERS, CommandName
from awesome_agent.version import PRODUCT_VERSION

ROOT = Path("src/awesome_agent")
TUI_ROOT = Path("tui")
REPOSITORY_ROOT = Path(".")
CURRENT_PACKAGES = {
    "agent",
    "application",
    "config",
    "context",
    "conversation",
    "core",
    "development",
    "extensions",
    "memory",
    "modeling",
    "protocol",
    "providers",
    "safety",
    "storage",
}
EXPECTED_DIRECT_DEPENDENCIES = {
    "langgraph",
    "langgraph-checkpoint-sqlite",
    "mcp",
    "openai",
    "pydantic",
    "python-dotenv",
    "pyyaml",
}
CURRENT_COMMANDS = {
    "auth",
    "compact",
    "config",
    "context",
    "copy",
    "diff",
    "doctor",
    "help",
    "init",
    "mcp",
    "memory",
    "model",
    "new",
    "permissions",
    "quit",
    "redo",
    "resume",
    "skills",
    "status",
    "theme",
    "thinking",
    "tools",
    "undo",
    "usage",
    "workspace",
}
SUPERSEDED_PRODUCT_PACKAGES = {
    "api",
    "artifacts",
    "persistence",
    "runtime",
    "sandbox",
    "surfaces",
    "tui",
    "worker",
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


def test_product_entrypoints_are_python_host_and_ink_cli() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((TUI_ROOT / "package.json").read_text(encoding="utf-8"))

    assert project["project"]["scripts"] == {
        "awesome-core": "awesome_agent.protocol.stdio:main",
        "awesome-dev": "awesome_agent.development.launcher:main",
    }
    assert package["bin"] == {"awesome": "dist/cli/index.js"}
    assert (ROOT / "protocol" / "stdio.py").is_file()
    assert (TUI_ROOT / "src" / "cli" / "index.ts").is_file()


def test_superseded_product_packages_cannot_reappear() -> None:
    present = {
        path.name
        for path in ROOT.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    }

    assert present == CURRENT_PACKAGES
    assert present.isdisjoint(SUPERSEDED_PRODUCT_PACKAGES)


def test_langgraph_compilation_belongs_to_agent_graph() -> None:
    current_source = tuple(
        path for package in CURRENT_PACKAGES for path in (ROOT / package).rglob("*.py")
    )
    state_graph_importers = {
        path.relative_to(ROOT).as_posix()
        for path in current_source
        if any(
            imported == "langgraph.graph" or imported.startswith("langgraph.graph.")
            for imported in _imports(path)
        )
    }
    compile_graph_mentions = {
        path.relative_to(ROOT).as_posix()
        for path in current_source
        if "compile_agent_graph" in path.read_text(encoding="utf-8")
    }

    assert state_graph_importers == {"agent/graph.py"}
    assert compile_graph_mentions == {
        "agent/__init__.py",
        "agent/graph.py",
        "application/composition.py",
    }


def test_direct_dependencies_match_current_production_contracts() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {
        re.split(r"[<>=!~\[]", value, maxsplit=1)[0].casefold()
        for value in project["project"]["dependencies"]
    }

    assert dependencies == EXPECTED_DIRECT_DEPENDENCIES
    assert set(project["project"]["optional-dependencies"]) == {"memory"}
    assert project["project"]["optional-dependencies"]["memory"] == ["mem0ai>=2.0.7,<3"]


def test_external_adapters_are_wired_at_composition() -> None:
    provider_importers = {
        path.name
        for path in (ROOT / "application").glob("*.py")
        if any(
            imported == "awesome_agent.providers"
            or imported.startswith("awesome_agent.providers.")
            for imported in _imports(path)
        )
    }

    assert provider_importers == {"composition.py"}


def test_command_inventory_is_current() -> None:
    assert set(COMMAND_OWNERS) == set(CommandName)
    assert {name.value for name in CommandName} == CURRENT_COMMANDS


def test_product_version_has_one_manual_source(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("AWESOME_VERSION", "9.9.9")
    expected = (REPOSITORY_ROOT / "VERSION").read_text(encoding="utf-8")

    assert expected == "1.1.1\n"
    assert distribution_version("awesome-agent") == "1.1.1"
    assert PRODUCT_VERSION == "1.1.1"

    project = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert project["project"]["dynamic"] == ["version"]
    assert "version" not in project["project"]
    assert project["tool"]["hatch"]["version"]["path"] == "VERSION"

    package = json.loads((TUI_ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((TUI_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert package["version"] == "1.1.1"
    assert lock["version"] == "1.1.1"
    assert lock["packages"][""]["version"] == "1.1.1"
    assert (TUI_ROOT / "src" / "version.ts").read_text(encoding="utf-8") == (
        'export const PRODUCT_VERSION = "1.1.1" as const;\n'
    )


def test_tui_is_one_minimal_node_22_package() -> None:
    package = json.loads((TUI_ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["name"] == "@awesome-agent/tui"
    assert package["version"] == PRODUCT_VERSION
    assert package["type"] == "module"
    assert package["engines"] == {"node": ">=22"}
    assert package["bin"] == {"awesome": "dist/cli/index.js"}
    assert package["files"] == ["dist", "README.md", "LICENSE"]
    assert package["license"] == "UNLICENSED"
    assert (TUI_ROOT / "package-lock.json").is_file()
    assert set(package["dependencies"]) == {
        "clipboardy",
        "ink",
        "marked",
        "react",
        "zod",
    }
    assert set(package["scripts"]) >= {
        "build",
        "format:check",
        "lint",
        "test",
        "typecheck",
    }


def test_tui_process_authority_is_confined_to_core_adapter() -> None:
    sources = tuple((TUI_ROOT / "src").rglob("*.ts")) + tuple(
        (TUI_ROOT / "src").rglob("*.tsx")
    )
    imports_by_path = {
        path: set(
            re.findall(
                r'\bfrom\s+["\']([^"\']+)["\']',
                path.read_text(encoding="utf-8"),
            )
        )
        for path in sources
    }
    node_importers = {
        path.relative_to(TUI_ROOT).as_posix(): {
            imported for imported in imports if imported.startswith("node:")
        }
        for path, imports in imports_by_path.items()
        if any(imported.startswith("node:") for imported in imports)
    }

    assert sources
    assert node_importers == {
        "src/cli/runtime-checks.ts": {"node:fs", "node:path"},
        "src/core/process.ts": {"node:child_process"},
        "src/preferences/paths.ts": {"node:os", "node:path"},
        "src/preferences/store.ts": {"node:fs/promises", "node:path"},
        "src/transcript/identity.ts": {"node:crypto"},
    }

    approved_node_imports = {
        imported for imports in node_importers.values() for imported in imports
    }
    allowed_external_imports = {
        "clipboardy",
        "ink",
        "marked",
        "react",
        "zod",
        *approved_node_imports,
    }
    external_imports = {
        imported
        for imports in imports_by_path.values()
        for imported in imports
        if not imported.startswith(".")
    }
    clipboard_importers = {
        path.relative_to(TUI_ROOT).as_posix()
        for path, imports in imports_by_path.items()
        if "clipboardy" in imports
    }

    assert external_imports <= allowed_external_imports
    assert clipboard_importers == {"src/adapters/clipboard.ts"}

    reducer_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (TUI_ROOT / "src" / "state").glob("*.ts")
    )
    assert "node:" not in reducer_source
    assert "react" not in reducer_source.casefold()
