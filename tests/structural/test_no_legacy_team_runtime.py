from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "awesome_agent"
LEGACY_ORCHESTRATION = ".".join(("awesome_agent", "orchestration"))
SCOPED_ROUTE = "team-coding" + "-scoped"
SCOPED_ROUTE_CONSTANT = "SCOPED_TEAM" + "_CODING_ROUTE"
SCOPED_GRAPH_CLASS = "Team" + "CodingGraph"


def _python_files(path: Path) -> list[Path]:
    return [
        item
        for item in path.rglob("*.py")
        if "__pycache__" not in item.parts
    ]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_legacy_orchestration_package_is_removed() -> None:
    assert not (SRC / "orchestration").exists()


def test_scoped_team_module_is_removed() -> None:
    assert not (SRC / "runtime" / ("team" + "_graph.py")).exists()


def test_production_code_does_not_import_legacy_orchestration() -> None:
    offenders = {
        path.relative_to(ROOT).as_posix(): sorted(
            item
            for item in _imports(path)
            if item == LEGACY_ORCHESTRATION
            or item.startswith(f"{LEGACY_ORCHESTRATION}.")
        )
        for path in _python_files(SRC)
    }
    offenders = {path: imports for path, imports in offenders.items() if imports}

    assert offenders == {}


def test_production_code_does_not_define_scoped_team_runtime() -> None:
    offenders: dict[str, list[str]] = {}
    forbidden = (
        SCOPED_ROUTE_CONSTANT,
        SCOPED_ROUTE,
        SCOPED_GRAPH_CLASS,
    )
    for path in _python_files(SRC):
        text = path.read_text(encoding="utf-8")
        hits = [item for item in forbidden if item in text]
        if hits:
            offenders[path.relative_to(ROOT).as_posix()] = hits

    assert offenders == {}
