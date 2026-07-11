from __future__ import annotations

import ast
import json
import re
import tomllib
from importlib.metadata import version as distribution_version
from pathlib import Path

from awesome_agent.application.commands import COMMAND_OWNERS, CommandName
from awesome_agent.version import PRODUCT_VERSION

ROOT = Path("src/awesome_agent")
TUI_ROOT = Path("tui")
REPOSITORY_ROOT = Path(".")
ENTRYPOINTS = (
    ROOT / "application" / "composition.py",
    ROOT / "protocol" / "stdio.py",
)
FORBIDDEN_INTERNAL = {
    "awesome_agent.agents",
    "awesome_agent.runtime",
    "awesome_agent.observability",
    "awesome_agent.orchestration",
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
LEGACY_SURFACE_PACKAGES = {"cli", "client", "surfaces", "tui"}
LEGACY_PLATFORM_PACKAGES = {"runtime", "agents", "observability", "orchestration"}
TARGET_PACKAGES = {
    "agent",
    "application",
    "config",
    "context",
    "conversation",
    "core",
    "extensions",
    "memory",
    "modeling",
    "protocol",
    "providers",
    "safety",
    "storage",
}
SERVICE_ASSETS = (
    Path("Dockerfile"),
    Path("docker-compose.yml"),
    Path(".dockerignore"),
    Path("sandbox"),
    Path("demo/index.html"),
)
EXPECTED_DIRECT_DEPENDENCIES = {
    "langgraph",
    "langgraph-checkpoint-sqlite",
    "mcp",
    "openai",
    "pydantic",
    "python-dotenv",
    "pyyaml",
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


def test_every_target_package_excludes_platform_runtime_imports() -> None:
    denied = {
        "awesome_agent.runtime",
        "awesome_agent.agents",
        "awesome_agent.observability",
        "awesome_agent.orchestration",
    }
    violations = {
        path.relative_to(ROOT).as_posix(): sorted(
            imported
            for imported in _imports(path)
            if any(
                imported == prefix or imported.startswith(f"{prefix}.")
                for prefix in denied
            )
        )
        for package in TARGET_PACKAGES
        for path in (ROOT / package).rglob("*.py")
    }

    assert not {path: imports for path, imports in violations.items() if imports}


def test_langgraph_compilation_belongs_only_to_agent_graph() -> None:
    target_source = tuple(
        path for package in TARGET_PACKAGES for path in (ROOT / package).rglob("*.py")
    )
    state_graph_importers = {
        path.relative_to(ROOT).as_posix()
        for path in target_source
        if any(
            imported == "langgraph.graph" or imported.startswith("langgraph.graph.")
            for imported in _imports(path)
        )
    }
    compile_graph_mentions = {
        path.relative_to(ROOT).as_posix()
        for path in target_source
        if "compile_agent_graph" in path.read_text(encoding="utf-8")
    }

    assert state_graph_importers == {"agent/graph.py"}
    assert compile_graph_mentions == {
        "agent/__init__.py",
        "agent/graph.py",
        "application/composition.py",
    }


def test_python_legacy_surfaces_are_physically_absent() -> None:
    assert not {
        name for name in LEGACY_SURFACE_PACKAGES if any((ROOT / name).rglob("*.py"))
    }

    retained_source = tuple(
        path for name in TARGET_PACKAGES for path in (ROOT / name).rglob("*.py")
    )
    violations = {
        path.relative_to(ROOT).as_posix(): sorted(
            imported
            for imported in _imports(path)
            if imported in {"textual", "typer"}
            or imported.startswith(("textual.", "typer."))
            or any(
                imported == f"awesome_agent.{name}"
                or imported.startswith(f"awesome_agent.{name}.")
                for name in LEGACY_SURFACE_PACKAGES
            )
        )
        for path in retained_source
    }
    assert not {path: imports for path, imports in violations.items() if imports}

    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {
        value.split("[", 1)[0].split(">", 1)[0]
        for value in project["project"]["dependencies"]
    }
    assert not {"textual", "typer"} & dependencies
    assert (Path("tui") / "src" / "cli" / "index.ts").is_file()
    assert (ROOT / "protocol" / "stdio.py").is_file()


def test_platform_runtime_packages_and_dependencies_are_physically_absent() -> None:
    assert not {
        name for name in LEGACY_PLATFORM_PACKAGES if any((ROOT / name).rglob("*.py"))
    }

    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert "observability" not in project["project"]["optional-dependencies"]


def test_api_and_container_product_paths_are_absent() -> None:
    assert not any((ROOT / "api").rglob("*.py"))
    assert not {
        path.as_posix()
        for path in SERVICE_ASSETS
        if path.is_file()
        or (path.is_dir() and any(item.is_file() for item in path.rglob("*")))
    }

    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {
        value.split("[", 1)[0].split(">", 1)[0]
        for value in project["project"]["dependencies"]
    }
    assert not {"fastapi", "uvicorn"} & dependencies
    assert project["project"]["scripts"] == {
        "awesome-core": "awesome_agent.protocol.stdio:main"
    }

    target_source = "\n".join(
        path.read_text(encoding="utf-8")
        for name in TARGET_PACKAGES
        for path in (ROOT / name).rglob("*.py")
    ).casefold()
    assert "awesome_agent.api" not in target_source
    assert "fastapi" not in target_source
    assert "uvicorn" not in target_source


def test_direct_dependencies_match_retained_production_imports() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {
        re.split(r"[<>=!~\[]", value, maxsplit=1)[0].casefold()
        for value in project["project"]["dependencies"]
    }

    assert dependencies == EXPECTED_DIRECT_DEPENDENCIES
    assert set(project["project"]["optional-dependencies"]) == {"memory"}
    assert project["project"]["optional-dependencies"]["memory"] == ["mem0ai>=2.0.7,<3"]


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


def test_product_version_has_one_manual_source(monkeypatch) -> None:
    monkeypatch.setenv("AWESOME_VERSION", "9.9.9")
    expected = (REPOSITORY_ROOT / "VERSION").read_text(encoding="utf-8")

    assert expected == "1.0.0\n"
    assert distribution_version("awesome-agent") == "1.0.0"
    assert PRODUCT_VERSION == "1.0.0"

    project = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert project["project"]["dynamic"] == ["version"]
    assert "version" not in project["project"]
    assert project["project"]["scripts"] == {
        "awesome-core": "awesome_agent.protocol.stdio:main"
    }
    assert project["tool"]["hatch"]["version"]["path"] == "VERSION"

    package = json.loads((TUI_ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((TUI_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert package["version"] == "1.0.0"
    assert lock["version"] == "1.0.0"
    assert lock["packages"][""]["version"] == "1.0.0"
    assert (TUI_ROOT / "src" / "version.ts").read_text(encoding="utf-8") == (
        'export const PRODUCT_VERSION = "1.0.0" as const;\n'
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
    assert "workspaces" not in package
    assert (TUI_ROOT / "package-lock.json").is_file()
    dependencies = set(package.get("dependencies", {}))
    assert not dependencies & {
        "@langchain/langgraph",
        "@modelcontextprotocol/sdk",
        "@anthropic-ai/sdk",
        "openai",
        "mem0ai",
        "@reduxjs/toolkit",
        "axios",
        "better-sqlite3",
        "dockerode",
        "execa",
        "express",
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


def test_tui_process_authority_is_confined_to_core_adapter() -> None:
    sources = tuple((TUI_ROOT / "src").rglob("*.ts")) + tuple(
        (TUI_ROOT / "src").rglob("*.tsx")
    )
    assert sources
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
    assert node_importers == {
        "src/cli/runtime-checks.ts": {"node:fs", "node:path"},
        "src/core/process.ts": {"node:child_process"},
        "src/preferences/paths.ts": {"node:os", "node:path"},
        "src/preferences/store.ts": {"node:fs/promises", "node:path"},
    }
    approved_node_imports = {
        imported for imports in node_importers.values() for imported in imports
    }
    clipboard_importers = {
        path.relative_to(TUI_ROOT).as_posix()
        for path, imports in imports_by_path.items()
        if "clipboardy" in imports
    }
    assert clipboard_importers == {"src/adapters/clipboard.ts"}
    assert all(
        imported == "zod"
        or imported == "clipboardy"
        or imported == "ink"
        or imported == "react"
        or imported.startswith(".")
        or imported in approved_node_imports
        for imports in imports_by_path.values()
        for imported in imports
    )
    external_imports = {
        imported
        for imports in imports_by_path.values()
        for imported in imports
        if not imported.startswith(".")
    }
    assert (
        not {
            "@anthropic-ai/sdk",
            "@langchain/langgraph",
            "@modelcontextprotocol/sdk",
            "better-sqlite3",
            "docker",
            "dockerode",
            "execa",
            "express",
            "fastapi",
            "node:http",
            "openai",
            "sqlalchemy",
            "textual",
        }
        & external_imports
    )

    source = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    assert "fetch(" not in source
    assert "awesome_agent.tui" not in source.casefold()

    reducer_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (TUI_ROOT / "src" / "state").glob("*.ts")
    )
    assert "node:" not in reducer_source
    assert "react" not in reducer_source.casefold()
