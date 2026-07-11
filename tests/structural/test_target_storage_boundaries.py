from __future__ import annotations

import ast
import sqlite3
import tomllib
from pathlib import Path

from awesome_agent.storage.database import initialize_application_database

FORBIDDEN_IMPORT_ROOTS = {
    "awesome_agent.api",
    "awesome_agent.persistence",
    "awesome_agent.providers",
    "awesome_agent.runtime",
    "awesome_agent.settings",
    "awesome_agent.surfaces",
    "awesome_agent.tui",
    "fastapi",
    "openai",
    "psycopg",
    "sqlalchemy",
    "textual",
    "langgraph.checkpoint.postgres",
}
LEGACY_STATE_PATHS = {
    Path("src/awesome_agent/persistence"),
    Path("src/awesome_agent/domain"),
    Path("src/awesome_agent/artifacts"),
    Path("src/awesome_agent/attachments"),
    Path("src/awesome_agent/repositories"),
    Path("src/awesome_agent/sandbox"),
    Path("src/awesome_agent/tools"),
    Path("migrations"),
    Path("alembic.ini"),
}
FORBIDDEN_PLATFORM_TABLES = {
    "events",
    "tool_invocations",
    "runs",
    "artifacts",
    "approvals",
    "workers",
    "leases",
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


def test_target_storage_does_not_import_current_platform_layers() -> None:
    storage_root = Path("src/awesome_agent/storage")

    assert storage_root.is_dir()
    violations = {
        path.as_posix(): sorted(
            imported
            for imported in _imports(path)
            if any(
                imported == root or imported.startswith(f"{root}.")
                for root in FORBIDDEN_IMPORT_ROOTS
            )
        )
        for path in storage_root.rglob("*.py")
    }

    assert {path: names for path, names in violations.items() if names} == {}


def test_legacy_state_packages_schemas_and_dependencies_are_absent() -> None:
    assert not {
        path.as_posix()
        for path in LEGACY_STATE_PATHS
        if path.is_file()
        or (path.is_dir() and any(item.is_file() for item in path.rglob("*")))
    }

    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert "postgres" not in project["project"]["optional-dependencies"]
    serialized = "\n".join(project["project"]["dependencies"]).casefold()
    assert not {
        dependency
        for dependency in {
            "alembic",
            "asyncpg",
            "langgraph-checkpoint-postgres",
            "psycopg",
            "sqlalchemy",
        }
        if dependency in serialized
    }


def test_target_composition_uses_only_embedded_state_owners() -> None:
    imports = _imports(Path("src/awesome_agent/application/composition.py"))
    forbidden = {
        "awesome_agent.persistence",
        "awesome_agent.domain",
        "awesome_agent.artifacts",
        "awesome_agent.attachments",
        "awesome_agent.repositories",
        "awesome_agent.sandbox",
        "awesome_agent.tools",
        "langgraph.checkpoint.postgres",
    }
    assert not {
        imported
        for imported in imports
        if any(
            imported == denied or imported.startswith(f"{denied}.")
            for denied in forbidden
        )
    }
    assert {
        "awesome_agent.storage",
        "awesome_agent.core.changes",
        "awesome_agent.memory",
        "langgraph.checkpoint.base",
    } <= imports


def test_application_database_has_no_platform_resource_tables(tmp_path: Path) -> None:
    database = tmp_path / "application.db"
    initialize_application_database(database)
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert not tables & FORBIDDEN_PLATFORM_TABLES
