import ast
from pathlib import Path

import pytest

from awesome_agent.core.changes import ChangeJournal
from awesome_agent.core.tools.builtins import (
    register_modifying_tools,
    register_read_tools,
)
from awesome_agent.core.tools.permissions import ToolCapability
from awesome_agent.core.tools.process import ProcessRunner
from awesome_agent.core.tools.registry import ToolRegistry, ToolReplaySafety
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage import ApplicationSQLite
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore


@pytest.mark.asyncio
async def test_baseline_tools_exist_without_fixing_total_tool_count(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    journal = ChangeJournal(
        SQLiteChangeSetStore(database),
        FileChangeBlobStore(tmp_path / "change-journal"),
        identity,
    )
    registry = ToolRegistry()
    register_read_tools(registry)
    register_modifying_tools(
        registry,
        journal,
        ProcessRunner(),
        workspace=identity,
    )

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
    } == {"glob", "grep", "ls", "read_file"}
    assert {spec.name: spec.capability for spec in specifications} == {
        "delete": ToolCapability.WORKSPACE_DELETE,
        "edit_file": ToolCapability.WORKSPACE_WRITE,
        "execute": ToolCapability.SHELL_EXECUTE,
        "glob": ToolCapability.WORKSPACE_READ,
        "grep": ToolCapability.WORKSPACE_READ,
        "ls": ToolCapability.WORKSPACE_READ,
        "read_file": ToolCapability.WORKSPACE_READ,
        "write_file": ToolCapability.WORKSPACE_WRITE,
    }
    assert {name: registry.replay_safety(name) for name in baseline} == {
        "delete": ToolReplaySafety.REPLAYABLE,
        "edit_file": ToolReplaySafety.REPLAYABLE,
        "execute": ToolReplaySafety.NON_REPLAYABLE,
        "glob": ToolReplaySafety.REPLAYABLE,
        "grep": ToolReplaySafety.REPLAYABLE,
        "ls": ToolReplaySafety.REPLAYABLE,
        "read_file": ToolReplaySafety.REPLAYABLE,
        "write_file": ToolReplaySafety.REPLAYABLE,
    }
    await database.aclose()


def test_executor_ast_contains_no_concrete_tool_name_policy() -> None:
    executor_path = (
        Path(__file__).parents[2]
        / "src"
        / "awesome_agent"
        / "core"
        / "tools"
        / "executor.py"
    )
    tree = ast.parse(executor_path.read_text(encoding="utf-8"))
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert string_literals.isdisjoint(
        {
            "delete",
            "edit_file",
            "execute",
            "glob",
            "grep",
            "ls",
            "read_file",
            "write_file",
        }
    )


def test_production_tool_registrations_declare_replay_safety() -> None:
    repository = Path(__file__).parents[2]
    production_paths = (
        repository
        / "src"
        / "awesome_agent"
        / "core"
        / "tools"
        / "builtins"
        / "__init__.py",
        repository / "src" / "awesome_agent" / "extensions" / "skills" / "tools.py",
        repository / "src" / "awesome_agent" / "extensions" / "mcp" / "adapter.py",
        repository / "src" / "awesome_agent" / "memory" / "tools.py",
    )

    for path in production_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            function_name = (
                call.func.attr
                if isinstance(call.func, ast.Attribute)
                else call.func.id
                if isinstance(call.func, ast.Name)
                else None
            )
            if function_name not in {"register", "RegisteredTool"}:
                continue
            assert "replay_safety" in {keyword.arg for keyword in call.keywords}, (
                f"{path} contains an implicit replay-safety registration"
            )
