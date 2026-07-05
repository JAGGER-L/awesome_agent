from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


LEGACY_RUNTIME_IMPORT_ALLOWLIST = {
    "src/awesome_agent/runtime/team_graph.py": [
        "awesome_agent.orchestration.team",
    ],
}


def test_runtime_legacy_orchestration_imports_are_allowlisted() -> None:
    runtime_files = [
        path
        for path in (REPO_ROOT / "src" / "awesome_agent" / "runtime").rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    offenders = {
        path.relative_to(REPO_ROOT).as_posix(): sorted(
            item
            for item in _imports(path)
            if item.startswith("awesome_agent.orchestration")
        )
        for path in runtime_files
    }
    offenders = {path: imports for path, imports in offenders.items() if imports}

    assert offenders == LEGACY_RUNTIME_IMPORT_ALLOWLIST


def test_api_layer_does_not_import_legacy_orchestration() -> None:
    api_files = [
        path
        for path in (REPO_ROOT / "src" / "awesome_agent" / "api").rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    offenders = {
        path.relative_to(REPO_ROOT).as_posix(): sorted(
            item
            for item in _imports(path)
            if item.startswith("awesome_agent.orchestration")
        )
        for path in api_files
    }
    offenders = {path: imports for path, imports in offenders.items() if imports}

    assert offenders == {}
