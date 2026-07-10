from __future__ import annotations

import ast
from pathlib import Path

LEGACY_FILES = {"builtin.py", "external.py", "compression.py"}
FORBIDDEN_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.client",
    "awesome_agent.modeling",
    "awesome_agent.persistence",
    "awesome_agent.providers",
    "awesome_agent.runtime",
    "awesome_agent.surfaces",
    "awesome_agent.tui",
    "httpx",
    "mem0",
    "openai",
    "requests",
    "sqlalchemy",
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


def test_legacy_provider_double_write_and_compression_modules_are_absent() -> None:
    root = Path("src/awesome_agent/memory")
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))

    assert not {path.name for path in root.iterdir()} & LEGACY_FILES
    assert not Path("src/awesome_agent/tools/memory.py").exists()
    assert "MemoryProvider" not in source
    assert "NoopMemoryProvider" not in source
    assert "provider registry" not in source.casefold()
    assert "sync_turn" not in source
    assert "run_id" not in source
    assert "agent_id" not in source


def test_local_memory_has_no_network_sdk_or_legacy_layer_dependency() -> None:
    root = Path("src/awesome_agent/memory")
    violations = {
        path.as_posix(): sorted(
            imported
            for imported in _imports(path)
            if any(
                imported == denied or imported.startswith(f"{denied}.")
                for denied in FORBIDDEN_IMPORTS
            )
        )
        for path in root.rglob("*.py")
    }

    assert {path: imports for path, imports in violations.items() if imports} == {}


def test_local_writes_exist_only_in_commands_and_visible_tool_handlers() -> None:
    application_turns = Path("src/awesome_agent/application/turns.py").read_text(
        encoding="utf-8"
    )
    agent_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/awesome_agent/agent").rglob("*.py")
    )
    tools = Path("src/awesome_agent/memory/tools.py").read_text(encoding="utf-8")
    headless = Path("src/awesome_agent/application/headless.py").read_text(
        encoding="utf-8"
    )

    assert "LocalMemoryService" not in application_turns
    assert "LocalMemoryService" not in agent_source
    assert "service.add(" in tools
    assert "service.replace(" in tools
    assert "service.remove(" in tools
    assert "service.add(" in headless
    assert "post_answer" not in tools


def test_paths_are_home_scoped_and_never_use_repository_root_memory() -> None:
    paths = Path("src/awesome_agent/paths.py").read_text(encoding="utf-8")
    local_file = Path("src/awesome_agent/memory/local_file.py").read_text(
        encoding="utf-8"
    )

    assert 'resolved_home / "memory" / "USER.md"' in paths
    assert 'self.workspaces_dir / workspace_key / "MEMORY.md"' in paths
    assert "Path.cwd" not in local_file
    assert 'workspace / "MEMORY.md"' not in local_file
    assert 'workspace / "skills"' not in local_file
