from __future__ import annotations

import ast
from pathlib import Path

from awesome_agent.core.changes import ChangeJournal
from awesome_agent.core.tools.builtins import (
    register_modifying_tools,
    register_read_tools,
)
from awesome_agent.core.tools.process import ProcessRunner
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore

FORBIDDEN_TOOL_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.approval",
    "awesome_agent.approvals",
    "awesome_agent.artifacts",
    "awesome_agent.attachments",
    "awesome_agent.domain",
    "awesome_agent.persistence",
    "awesome_agent.repositories",
    "awesome_agent.runtime",
    "awesome_agent.sandbox",
    "awesome_agent.surfaces",
    "awesome_agent.tools",
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


def test_stable_baseline_tools_exist_without_fixing_total_tool_count(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)
    journal = ChangeJournal(
        SQLiteChangeSetStore(tmp_path / "application.db"),
        FileChangeBlobStore(tmp_path / "change-journal"),
        identity,
    )
    registry = ToolRegistry()
    register_read_tools(registry)
    register_modifying_tools(registry, journal, ProcessRunner())

    specifications = registry.specifications()
    baseline = {
        "delete": "Delete a file, or a directory and its contents recursively",
        "edit_file": "Perform exact string replacements in files",
        "execute": "Run shell commands",
        "glob": "Find files matching a glob pattern",
        "grep": "Search file contents",
        "ls": "List files in a directory",
        "read_file": "Read file contents",
        "write_file": "Create a new file, or overwrite an existing one",
    }
    effective = {spec.name: spec.description for spec in specifications}
    assert baseline.items() <= effective.items()
    assert {
        name
        for name in baseline
        if next(spec for spec in specifications if spec.name == name).read_only
    } == {
        "glob",
        "grep",
        "ls",
        "read_file",
    }


def test_target_tools_do_not_import_legacy_execution_layers() -> None:
    tools_root = Path("src/awesome_agent/core/tools")

    violations = {
        path.as_posix(): sorted(
            imported
            for imported in _imports(path)
            if any(
                imported == denied or imported.startswith(f"{denied}.")
                for denied in FORBIDDEN_TOOL_IMPORTS
            )
        )
        for path in tools_root.rglob("*.py")
    }
    assert {path: imports for path, imports in violations.items() if imports} == {}
