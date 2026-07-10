from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_IMPORT_ROOTS = {
    "awesome_agent.api",
    "awesome_agent.persistence",
    "awesome_agent.runtime",
    "awesome_agent.surfaces",
    "psycopg",
    "sqlalchemy",
    "langgraph.checkpoint.postgres",
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
