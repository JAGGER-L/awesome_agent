import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.extensions.catalog import publish_catalog
from awesome_agent.extensions.community import (
    CommunityToolPackageSource,
    register_community_tools,
)
from awesome_agent.extensions.models import ExtensionConfigError
from awesome_agent.extensions.sources import ExtensionSourceFactory
from awesome_agent.tools.approval import ApprovalPolicy
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.models import ApprovalRequired, ToolDenied, ToolInvocation
from awesome_agent.tools.registry import ToolRegistry


def test_community_package_manifest_discovers_namespaced_tool(tmp_path: Path) -> None:
    package = _write_package(tmp_path, package_id="web-search-basic")
    source = CommunityToolPackageSource(root=package, allowlisted_roots=[tmp_path])

    snapshot = asyncio.run(source.discover())

    assert snapshot.source.id == "community.web-search-basic"
    assert snapshot.tools[0].name == "community.web-search-basic.search"
    assert snapshot.tools[0].source_id == "community.web-search-basic"
    assert snapshot.tools[0].required_capabilities == {"network:request"}


def test_community_package_outside_allowlisted_root_is_rejected(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    denied = tmp_path / "denied"
    allowed.mkdir()
    denied.mkdir()

    with pytest.raises(ExtensionConfigError, match="allowlisted"):
        CommunityToolPackageSource(root=denied, allowlisted_roots=[allowed])


def test_community_package_rejects_unknown_handler_type(tmp_path: Path) -> None:
    package = _write_package(
        tmp_path,
        package_id="bad-handler",
        handler_type="python_import",
    )
    source = CommunityToolPackageSource(root=package, allowlisted_roots=[tmp_path])

    with pytest.raises(ExtensionConfigError, match="handler"):
        asyncio.run(source.discover())


def test_community_source_factory_builds_package_source(tmp_path: Path) -> None:
    package = _write_package(tmp_path, package_id="factory-package")
    source = ExtensionSourceFactory().create(
        {"id": "ignored", "type": "community_tool_package", "path": package}
    )

    snapshot = asyncio.run(source.discover())

    assert snapshot.tools[0].name == "community.factory-package.search"


@pytest.mark.asyncio
async def test_community_tool_executes_through_tool_executor(tmp_path: Path) -> None:
    package = _write_package(tmp_path, package_id="web-search-basic")
    _write_subprocess_tool(package, {"items": ["alpha"]})
    source = CommunityToolPackageSource(root=package, allowlisted_roots=[tmp_path])
    snapshot = await source.discover()
    catalog = publish_catalog(
        sources=[snapshot.source],
        tools=snapshot.tools,
        skills=[],
    )
    registry = ToolRegistry()

    register_community_tools(
        registry,
        source=source,
        catalog=catalog,
        exposed_tool_names={"community.web-search-basic.search"},
    )

    result = await ToolExecutor(registry, ApprovalPolicy()).execute(
        ToolInvocation(
            tool_name="community.web-search-basic.search",
            agent_id=uuid4(),
            profile="leader",
            capabilities={"network:request"},
            effective_tool_names={"community.web-search-basic.search"},
            arguments={"query": "alpha"},
        )
    )

    assert result.output["status"] == "ok"
    assert result.output["community"] == {
        "package_id": "web-search-basic",
        "tool": "search",
        "risk_level": "medium",
    }
    assert result.output["result"] == {"items": ["alpha"]}
    assert result.output["arguments_hash"]


@pytest.mark.asyncio
async def test_community_tool_stays_hidden_until_exposed(tmp_path: Path) -> None:
    package = _write_package(tmp_path, package_id="web-search-basic")
    _write_subprocess_tool(package, {"items": []})
    source = CommunityToolPackageSource(root=package, allowlisted_roots=[tmp_path])
    snapshot = await source.discover()
    catalog = publish_catalog(
        sources=[snapshot.source],
        tools=snapshot.tools,
        skills=[],
    )
    registry = ToolRegistry()

    register_community_tools(
        registry,
        source=source,
        catalog=catalog,
        exposed_tool_names={"community.web-search-basic.search"},
    )

    with pytest.raises(ToolDenied):
        await ToolExecutor(registry, ApprovalPolicy()).execute(
            ToolInvocation(
                tool_name="community.web-search-basic.search",
                agent_id=uuid4(),
                profile="leader",
                capabilities={"network:request"},
                effective_tool_names=set(),
                arguments={"query": "alpha"},
            )
        )


@pytest.mark.asyncio
async def test_high_risk_community_tool_requires_approval(tmp_path: Path) -> None:
    package = _write_package(
        tmp_path,
        package_id="writer",
        risk_level="high",
        required_capabilities=["workspace:write"],
    )
    _write_subprocess_tool(package, {"ok": True})
    source = CommunityToolPackageSource(root=package, allowlisted_roots=[tmp_path])
    snapshot = await source.discover()
    catalog = publish_catalog(
        sources=[snapshot.source],
        tools=snapshot.tools,
        skills=[],
    )
    registry = ToolRegistry()
    register_community_tools(
        registry,
        source=source,
        catalog=catalog,
        exposed_tool_names={"community.writer.search"},
    )

    spec, _ = registry.resolve("community.writer.search")
    assert spec.risk_level is RiskLevel.HIGH

    with pytest.raises(ApprovalRequired):
        await ToolExecutor(registry, ApprovalPolicy()).execute(
            ToolInvocation(
                tool_name="community.writer.search",
                agent_id=uuid4(),
                profile="leader",
                capabilities={"workspace:write"},
                effective_tool_names={"community.writer.search"},
                arguments={"query": "alpha"},
            )
        )


def _write_package(
    tmp_path: Path,
    *,
    package_id: str,
    handler_type: str = "subprocess_json",
    risk_level: str = "medium",
    required_capabilities: list[str] | None = None,
) -> Path:
    package = tmp_path / package_id
    package.mkdir()
    (package / "awesome-agent-community.json").write_text(
        json.dumps(
            {
                "id": package_id,
                "version": "1",
                "trust": "user",
                "tools": [
                    {
                        "name": "search",
                        "description": "Search with a configured provider.",
                        "risk_level": risk_level,
                        "required_capabilities": required_capabilities
                        or ["network:request"],
                        "input_schema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                        "handler": {
                            "type": handler_type,
                            "command": ["python", "tool.py"],
                            "timeout_seconds": 5,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return package


def _write_subprocess_tool(package: Path, output: dict[str, object]) -> None:
    script = (
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "assert isinstance(payload, dict)\n"
        f"print(json.dumps({json.dumps(output)}))\n"
    )
    (package / "tool.py").write_text(script, encoding="utf-8")
