from __future__ import annotations

import ast
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from awesome_agent.storage.database import initialize_application_database

STORAGE_MODULES = {
    "__init__.py",
    "changes.py",
    "checkpoints.py",
    "compatibility.py",
    "conversations.py",
    "database.py",
    "health.py",
    "mcp.py",
    "pagination.py",
    "state_lease.py",
    "state_recovery.py",
    "trust.py",
}
APPLICATION_TABLES = {
    "change_sets",
    "mcp_enablements",
    "pending_mutations",
    "thread_entries",
    "thread_summaries",
    "threads",
    "tool_activities",
    "trusted_workspaces",
    "turns",
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


def test_storage_module_inventory_is_current() -> None:
    assert {
        path.name for path in Path("src/awesome_agent/storage").glob("*.py")
    } == STORAGE_MODULES


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_state_lease_platform_bindings_type_check(
    platform: str,
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--no-incremental",
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
            "--strict",
            "--platform",
            platform,
            "src/awesome_agent/storage/state_lease.py",
        ],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_composition_uses_embedded_state_owners() -> None:
    imports = _imports(Path("src/awesome_agent/application/composition.py"))

    assert {
        "awesome_agent.storage",
        "awesome_agent.core.changes",
        "awesome_agent.memory",
        "langgraph.checkpoint.base",
    } <= imports


def test_application_database_has_current_tables(tmp_path: Path) -> None:
    database = tmp_path / "application.db"
    initialize_application_database(database)
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert tables == APPLICATION_TABLES
