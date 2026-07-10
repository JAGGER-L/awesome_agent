from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_CORE_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.application",
    "awesome_agent.persistence",
    "awesome_agent.runtime",
    "awesome_agent.storage",
    "awesome_agent.surfaces",
    "awesome_agent.tools",
    "awesome_agent.tui",
    "fastapi",
    "langgraph",
    "sqlalchemy",
}

FORBIDDEN_STORAGE_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.persistence",
    "awesome_agent.runtime",
    "awesome_agent.surfaces",
    "psycopg",
    "sqlalchemy",
    "langgraph.checkpoint.postgres",
}

FORBIDDEN_APPLICATION_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.client",
    "awesome_agent.persistence",
    "awesome_agent.runtime",
    "awesome_agent.settings",
    "awesome_agent.surfaces",
    "awesome_agent.tools",
    "awesome_agent.tui",
}

FORBIDDEN_EVENT_IMPORTS = {
    "awesome_agent.persistence",
    "awesome_agent.repositories",
    "awesome_agent.storage",
    "asyncio.queues",
    "logging",
    "loguru",
    "queue",
    "sqlite3",
    "structlog",
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


def _violations(root: Path, forbidden: set[str]) -> dict[str, list[str]]:
    violations = {
        path.as_posix(): sorted(
            imported
            for imported in _imports(path)
            if any(
                imported == denied or imported.startswith(f"{denied}.")
                for denied in forbidden
            )
        )
        for path in root.rglob("*.py")
    }
    return {path: imports for path, imports in violations.items() if imports}


def test_target_core_does_not_import_storage_or_platform_layers() -> None:
    core_root = Path("src/awesome_agent/core")

    assert core_root.is_dir()
    assert _violations(core_root, FORBIDDEN_CORE_IMPORTS) == {}


def test_target_storage_retains_its_platform_boundary() -> None:
    storage_root = Path("src/awesome_agent/storage")

    assert storage_root.is_dir()
    assert _violations(storage_root, FORBIDDEN_STORAGE_IMPORTS) == {}


def test_target_application_does_not_import_legacy_platform_layers() -> None:
    application_root = Path("src/awesome_agent/application")

    assert application_root.is_dir()
    assert _violations(application_root, FORBIDDEN_APPLICATION_IMPORTS) == {}


def test_live_events_do_not_import_persistence_or_delivery_backends() -> None:
    events_path = Path("src/awesome_agent/core/events.py")

    assert events_path.is_file()
    assert not {
        imported
        for imported in _imports(events_path)
        if any(
            imported == denied or imported.startswith(f"{denied}.")
            for denied in FORBIDDEN_EVENT_IMPORTS
        )
    }
