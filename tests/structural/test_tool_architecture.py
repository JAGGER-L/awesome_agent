from pathlib import Path

from awesome_agent.core.changes import ChangeJournal
from awesome_agent.core.tools.builtins import (
    register_modifying_tools,
    register_read_tools,
)
from awesome_agent.core.tools.permissions import ToolCapability
from awesome_agent.core.tools.process import ProcessRunner
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore


def test_baseline_tools_exist_without_fixing_total_tool_count(tmp_path: Path) -> None:
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
    } == {"glob", "grep", "ls", "read_file"}
    assert {
        spec.name: spec.capability for spec in specifications
    } == {
        "delete": ToolCapability.WORKSPACE_DELETE,
        "edit_file": ToolCapability.WORKSPACE_WRITE,
        "execute": ToolCapability.SHELL_EXECUTE,
        "glob": ToolCapability.WORKSPACE_READ,
        "grep": ToolCapability.WORKSPACE_READ,
        "ls": ToolCapability.WORKSPACE_READ,
        "read_file": ToolCapability.WORKSPACE_READ,
        "write_file": ToolCapability.WORKSPACE_WRITE,
    }
