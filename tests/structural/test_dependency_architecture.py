from __future__ import annotations

import ast
from pathlib import Path

ALLOWED_INTERNAL_IMPORTS = {
    "agent": {"agent", "core", "memory", "modeling"},
    "application": {
        "agent",
        "application",
        "config",
        "context",
        "conversation",
        "core",
        "extensions",
        "memory",
        "modeling",
        "paths",
        "providers",
        "safety",
        "storage",
        "version",
    },
    "config": {"config", "core", "paths"},
    "context": {"context", "conversation", "core", "memory", "modeling"},
    "conversation": {"config", "conversation"},
    "core": {"core", "safety"},
    "extensions": {"context", "core", "extensions"},
    "memory": {"config", "core", "memory", "modeling", "paths", "safety"},
    "modeling": {"config", "modeling"},
    "protocol": {"application", "core", "paths", "protocol", "version"},
    "providers": {"config", "modeling", "providers"},
    "safety": {"modeling", "safety"},
    "storage": {"agent", "conversation", "core", "extensions", "storage"},
}

EXTERNAL_FRAMEWORK_OWNERS = {
    "jsonschema": {"extensions"},
    "langgraph": {"agent", "application", "storage"},
    "mcp": {"extensions"},
    "openai": {"providers"},
    "sqlite3": {"storage"},
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _internal_root(imported: str) -> str | None:
    parts = imported.split(".")
    if len(parts) < 2 or parts[0] != "awesome_agent":
        return None
    return parts[1]


def test_internal_imports_follow_current_dependency_map() -> None:
    package_root = Path("src/awesome_agent")
    violations: dict[str, list[str]] = {}
    for package, allowed in ALLOWED_INTERNAL_IMPORTS.items():
        for path in (package_root / package).rglob("*.py"):
            denied = sorted(
                imported
                for imported in _imports(path)
                if (root := _internal_root(imported)) is not None
                and root not in allowed
            )
            if denied:
                violations[path.as_posix()] = denied

    assert violations == {}


def test_external_frameworks_have_current_owners() -> None:
    package_root = Path("src/awesome_agent")
    violations: dict[str, list[str]] = {}
    for package in ALLOWED_INTERNAL_IMPORTS:
        for path in (package_root / package).rglob("*.py"):
            denied = sorted(
                imported
                for imported in _imports(path)
                for framework, owners in EXTERNAL_FRAMEWORK_OWNERS.items()
                if (imported == framework or imported.startswith(f"{framework}."))
                and package not in owners
            )
            if denied:
                violations[path.as_posix()] = denied

    assert violations == {}


def test_change_recovery_boundary_does_not_depend_on_tools() -> None:
    changes_root = Path("src/awesome_agent/core/changes")
    violations = {
        path.as_posix(): sorted(
            imported
            for imported in _imports(path)
            if imported == "awesome_agent.core.tools"
            or imported.startswith("awesome_agent.core.tools.")
        )
        for path in changes_root.rglob("*.py")
    }

    assert {path: imports for path, imports in violations.items() if imports} == {}


def test_change_recovery_does_not_reopen_targets_with_path_helpers() -> None:
    forbidden = {
        "exists",
        "is_dir",
        "is_file",
        "is_symlink",
        "lstat",
        "mkdir",
        "read_bytes",
        "read_text",
        "rename",
        "replace",
        "resolve",
        "rmdir",
        "symlink_to",
        "unlink",
        "write_bytes",
        "write_text",
    }
    violations: dict[str, list[tuple[int, str]]] = {}
    for path in (
        Path("src/awesome_agent/core/changes/journal.py"),
        Path("src/awesome_agent/core/changes/operations.py"),
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        denied = sorted(
            (node.lineno, node.func.attr)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden
        )
        if denied:
            violations[path.as_posix()] = denied

    assert violations == {}
