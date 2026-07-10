from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_APPLICATION_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.artifacts",
    "awesome_agent.client",
    "awesome_agent.persistence",
    "awesome_agent.providers",
    "awesome_agent.runtime",
    "awesome_agent.sandbox",
    "awesome_agent.surfaces",
    "awesome_agent.tools",
    "awesome_agent.tui",
    "awesome_agent.worker",
    "awesome_agent.settings",
    "fastapi",
    "docker",
    "sqlalchemy",
    "textual",
}

FORBIDDEN_HEADLESS_TEST_MARKERS = {
    "postgresql://",
    "docker run",
    "fastapi",
    "uvicorn",
    "awesome_agent.providers",
    "langgraph",
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


def test_target_application_does_not_import_legacy_or_surface_layers() -> None:
    root = Path("src/awesome_agent/application")

    violations = {
        path.as_posix(): sorted(
            imported
            for imported in _imports(path)
            if not (
                path.name == "composition.py"
                and (
                    imported == "awesome_agent.providers"
                    or imported.startswith("awesome_agent.providers.")
                )
            )
            if any(
                imported == denied or imported.startswith(f"{denied}.")
                for denied in FORBIDDEN_APPLICATION_IMPORTS
            )
        )
        for path in root.rglob("*.py")
    }
    assert {path: imports for path, imports in violations.items() if imports} == {}


def test_headless_acceptance_uses_no_external_service_or_environment_hooks() -> None:
    path = Path("tests/integration/test_headless_foundation.py")
    source = path.read_text(encoding="utf-8")
    lowered = source.casefold()

    assert not {
        marker for marker in FORBIDDEN_HEADLESS_TEST_MARKERS if marker in lowered
    }
    tree = ast.parse(source)
    assert not {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}
    }
