from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_worker_app_does_not_construct_scoped_team_graph() -> None:
    text = (ROOT / "src" / "awesome_agent" / "runtime" / "worker_app.py").read_text(
        encoding="utf-8"
    )

    assert "TeamCodingGraph(" not in text
    assert "SCOPED_TEAM_CODING_ROUTE" not in text
    assert "team_provider_resolver" not in text


def test_legacy_orchestration_import_is_confined_to_scoped_team_graph() -> None:
    runtime_files = [
        path
        for path in (ROOT / "src" / "awesome_agent" / "runtime").rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    offenders = {
        path.relative_to(ROOT).as_posix(): sorted(
            item
            for item in _imports(path)
            if item.startswith("awesome_agent.orchestration")
        )
        for path in runtime_files
    }
    offenders = {path: imports for path, imports in offenders.items() if imports}

    assert offenders == {
        "src/awesome_agent/runtime/team_graph.py": [
            "awesome_agent.orchestration.team"
        ]
    }
