from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from awesome_agent.storage.database import initialize_application_database

STORAGE_MODULES = {
    "__init__.py",
    "changes.py",
    "checkpoints.py",
    "conversations.py",
    "database.py",
    "mcp.py",
    "pagination.py",
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
