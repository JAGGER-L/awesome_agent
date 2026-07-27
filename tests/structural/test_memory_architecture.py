from __future__ import annotations

import ast
import tomllib
from pathlib import Path

MEMORY_MODULES = {
    "__init__.py",
    "distiller.py",
    "finalization.py",
    "identity.py",
    "local_file.py",
    "mem0_cloud.py",
    "models.py",
    "policy.py",
    "service.py",
    "tools.py",
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


def test_memory_module_inventory_is_current() -> None:
    assert {
        path.name for path in Path("src/awesome_agent/memory").glob("*.py")
    } == MEMORY_MODULES


def test_cloud_dependencies_have_explicit_owners() -> None:
    root = Path("src/awesome_agent/memory")
    modeling_importers = {
        path.name
        for path in root.glob("*.py")
        if any(
            imported == "awesome_agent.modeling"
            or imported.startswith("awesome_agent.modeling.")
            for imported in _imports(path)
        )
    }
    sdk_importers = {
        path.name
        for path in root.glob("*.py")
        if any(
            imported == "mem0" or imported.startswith("mem0.")
            for imported in _imports(path)
        )
    }

    assert modeling_importers == {"distiller.py", "finalization.py"}
    assert sdk_importers == {"mem0_cloud.py"}


def test_agent_finalizer_port_is_the_only_memory_to_agent_dependency() -> None:
    agent_imports = {
        path.name: {
            imported
            for imported in _imports(path)
            if imported == "awesome_agent.agent"
            or imported.startswith("awesome_agent.agent.")
        }
        for path in Path("src/awesome_agent/memory").glob("*.py")
    }

    assert {name: imports for name, imports in agent_imports.items() if imports} == {
        "finalization.py": {"awesome_agent.agent.finalization"}
    }


def test_mem0_cloud_is_one_optional_adapter() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["optional-dependencies"]["memory"] == [
        "mem0ai>=2.0.7,<3"
    ]


def test_cloud_metadata_and_credentials_have_one_safe_authority() -> None:
    adapter = Path("src/awesome_agent/memory/mem0_cloud.py").read_text(encoding="utf-8")
    credential_catalog = Path(
        "src/awesome_agent/config/credential_catalog.py"
    ).read_text(encoding="utf-8")
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
    for private_field in (
        '"username"',
        '"email"',
        '"absolute_path"',
        '"repository"',
        '"git_remote"',
    ):
        assert private_field not in adapter
    assert '"MEM0_API_KEY"' in credential_catalog
    assert "mem0_api_key" not in workspace_config
    assert "mem0_cloud" not in workspace_config


def test_local_writes_are_visible_commands_or_tools() -> None:
    application_turns = Path("src/awesome_agent/application/turns.py").read_text(
        encoding="utf-8"
    )
    agent_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/awesome_agent/agent").rglob("*.py")
    )
    tools = Path("src/awesome_agent/memory/tools.py").read_text(encoding="utf-8")
    extension_commands = Path(
        "src/awesome_agent/application/extension_commands.py"
    ).read_text(encoding="utf-8")

    assert "LocalMemoryService" not in application_turns
    assert "LocalMemoryService" not in agent_source
    assert "service.add(" in tools
    assert "service.replace(" in tools
    assert "service.remove(" in tools
    assert "service.add(" in extension_commands
    assert "post_answer" not in tools


def test_paths_are_home_scoped() -> None:
    paths = Path("src/awesome_agent/paths.py").read_text(encoding="utf-8")
    local_file = Path("src/awesome_agent/memory/local_file.py").read_text(
        encoding="utf-8"
    )

    assert 'resolved_home / "memory" / "USER.md"' in paths
    assert 'self.workspaces_dir / workspace_key / "MEMORY.md"' in paths
    assert "Path.cwd" not in local_file
    assert 'workspace / "MEMORY.md"' not in local_file
    assert 'workspace / "skills"' not in local_file
