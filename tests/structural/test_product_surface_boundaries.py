from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path("src/awesome_agent")
TUI_ROOT = ROOT / "tui"
SHARED_SURFACE_ROOTS = [ROOT / "client", ROOT / "surfaces"]


def test_tui_does_not_import_runtime_execution_internals() -> None:
    forbidden_prefixes = (
        "awesome_agent.runtime.agent_loop",
        "awesome_agent.providers",
    )
    for path in _python_files(TUI_ROOT):
        imports = _imports(path)
        assert not [
            name
            for name in imports
            if name.startswith(forbidden_prefixes)
            or _is_runtime_graph_module(name)
        ], f"{path} imports runtime execution internals: {imports}"


def test_shared_surface_layers_do_not_import_textual() -> None:
    for root in SHARED_SURFACE_ROOTS:
        for path in _python_files(root):
            imports = _imports(path)
            assert not [
                name for name in imports if name == "textual" or name.startswith("textual.")
            ], f"{path} imports Textual: {imports}"


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.py"))


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _is_runtime_graph_module(name: str) -> bool:
    if not name.startswith("awesome_agent.runtime."):
        return False
    return name.rsplit(".", maxsplit=1)[-1].endswith("_graph")
