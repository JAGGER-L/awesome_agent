from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_AGENT_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.application",
    "awesome_agent.persistence",
    "awesome_agent.providers",
    "awesome_agent.runtime",
    "awesome_agent.sandbox",
    "awesome_agent.storage",
    "awesome_agent.surfaces",
    "awesome_agent.tui",
    "awesome_agent.worker",
    "docker",
    "fastapi",
    "sqlalchemy",
    "textual",
}
FORBIDDEN_STATE_FIELDS = {
    "client",
    "connection",
    "executor",
    "node_name",
    "position",
    "run_status",
    "service",
    "task",
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


def test_agent_depends_only_on_neutral_inner_contracts() -> None:
    root = Path("src/awesome_agent/agent")
    violations = {
        path.as_posix(): sorted(
            imported
            for imported in _imports(path)
            if any(
                imported == denied or imported.startswith(f"{denied}.")
                for denied in FORBIDDEN_AGENT_IMPORTS
            )
        )
        for path in root.rglob("*.py")
    }

    assert {path: imports for path, imports in violations.items() if imports} == {}


def test_agent_state_contains_data_not_runtime_position_or_services() -> None:
    path = Path("src/awesome_agent/agent/state.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    state = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AgentState"
    )
    field_names = {
        node.target.id
        for node in state.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    class_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }

    assert not field_names & FORBIDDEN_STATE_FIELDS
    assert "RunStatus" not in class_names
    assert "node" not in field_names
