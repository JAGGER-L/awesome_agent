from __future__ import annotations

import ast
import tomllib
from pathlib import Path

LEGACY_FILES = {"builtin.py", "external.py", "compression.py"}
FORBIDDEN_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.client",
    "awesome_agent.persistence",
    "awesome_agent.providers",
    "awesome_agent.runtime",
    "awesome_agent.surfaces",
    "awesome_agent.tui",
    "httpx",
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


def test_cloud_dependencies_are_confined_to_the_two_explicit_boundaries() -> None:
    root = Path("src/awesome_agent/memory")
    modeling_importers = {
        path.name
        for path in root.rglob("*.py")
        if any(
            imported == "awesome_agent.modeling"
            or imported.startswith("awesome_agent.modeling.")
            for imported in _imports(path)
        )
    }
    sdk_importers = {
        path.name
        for path in root.rglob("*.py")
        if any(
            imported == "mem0" or imported.startswith("mem0.")
            for imported in _imports(path)
        )
    }

    assert modeling_importers == {"distiller.py"}
    assert sdk_importers == {"mem0_cloud.py"}


def test_mem0_is_one_bounded_adapter_without_platform_infrastructure() -> None:
    root = Path("src/awesome_agent/memory")
    source_files = tuple(root.rglob("*.py"))
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert [path.name for path in source_files].count("mem0_cloud.py") == 1
    assert pyproject["project"]["optional-dependencies"]["memory"] == [
        "mem0ai>=2.0.7,<3"
    ]
    for forbidden in (
        "MemoryProvider",
        "MemoryWorker",
        "memory_queue",
        "memory_poll",
        "memory_sync_table",
        "sync_daemon",
        "double_write",
    ):
        assert forbidden not in source


def test_cloud_metadata_and_credentials_have_one_safe_authority() -> None:
    adapter = Path("src/awesome_agent/memory/mem0_cloud.py").read_text(encoding="utf-8")
    loader = Path("src/awesome_agent/config/loader.py").read_text(encoding="utf-8")
    workspace_config = (
        Path("src/awesome_agent/config/models.py")
        .read_text(encoding="utf-8")
        .split("class WorkspaceConfigDocument", 1)[1]
        .split("class SecretStatus", 1)[0]
    )

    assert '"app_id"' in adapter
    assert '"scope"' in adapter
    assert '"workspace_key"' in adapter
    assert '"fact_hash"' in adapter
    for forbidden in (
        '"username"',
        '"email"',
        '"absolute_path"',
        '"repository"',
        '"git_remote"',
    ):
        assert forbidden not in adapter
    assert '"MEM0_API_KEY"' in loader
    assert "mem0_api_key" not in workspace_config
    assert "mem0_cloud" not in workspace_config


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
