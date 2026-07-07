from __future__ import annotations

from pathlib import Path

from tests.type_helpers import test_settings

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.extensions.assembly import assemble_runtime_tools
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionHealthSnapshot,
    ExtensionHealthStatus,
    ExtensionSourceConfig,
    ExtensionSourceSnapshot,
    ExtensionSourceType,
    ExtensionToolInventoryItem,
    ExtensionTrustLevel,
)


def test_assembly_exposes_public_builtin_tools_to_capability_surface(
    tmp_path: Path,
) -> None:
    assembly = assemble_runtime_tools(
        project_root=tmp_path,
        settings=test_settings(local_state_dir=tmp_path / "state"),
    )

    tool_names = {spec.name for spec in assembly.tool_registry.list_specs()}
    surface_names = {
        item["name"]
        for group in assembly.capability_surface.tools().values()
        for item in group
    }

    public_tools = {
        "ReadFile",
        "FindFile",
        "WriteFile",
        "EditFile",
        "Bash",
        "Glob",
        "Grep",
    }
    assert {"repo.read", "repo.apply_patch", "shell.execute"}.issubset(tool_names)
    assert public_tools.issubset(tool_names)
    assert public_tools.issubset(surface_names)
    assert "repo.read" not in surface_names
    assert "repo.apply_patch" not in surface_names
    assert "shell.execute" not in surface_names


def test_assembly_marks_catalog_tool_enabled_when_registered(tmp_path: Path) -> None:
    catalog = ExtensionCatalog(
        version="test",
        sources=[
            ExtensionSourceSnapshot(
                id="community.fixture",
                type=ExtensionSourceType.COMMUNITY_TOOL_PACKAGE,
                trust=ExtensionTrustLevel.PROJECT,
                health=ExtensionHealthSnapshot(status=ExtensionHealthStatus.HEALTHY),
            )
        ],
        tools=[
            ExtensionToolInventoryItem(
                name="community.fixture.search",
                source_id="community.fixture",
                description="Search fixture.",
                risk_level=RiskLevel.LOW,
                required_capabilities={"network:request"},
            )
        ],
    )
    assembly = assemble_runtime_tools(
        project_root=tmp_path,
        settings=test_settings(local_state_dir=tmp_path / "state"),
        catalog=catalog,
        source_configs=(
            ExtensionSourceConfig(
                id="community.fixture",
                type=ExtensionSourceType.COMMUNITY_TOOL_PACKAGE,
                path=str(tmp_path),
            ),
        ),
    )

    groups = assembly.capability_surface.tools()
    item = groups["extension"][0]
    assert item["name"] == "community.fixture.search"
    assert item["enabled"] is True
