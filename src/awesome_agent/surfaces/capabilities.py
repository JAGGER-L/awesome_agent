from __future__ import annotations

from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionSourceSnapshot,
    ExtensionSourceType,
)
from awesome_agent.tools.registry import ToolRegistry


class CapabilitySurfaceService:
    def __init__(
        self,
        *,
        catalog: ExtensionCatalog,
        tool_registry: ToolRegistry,
    ) -> None:
        self.catalog = catalog
        self.tool_registry = tool_registry

    def tools(self) -> dict[str, list[dict[str, object]]]:
        groups: dict[str, list[dict[str, object]]] = {
            "builtin": [],
            "sandbox": [],
            "mcp": [],
            "extension": [],
        }
        catalog_tool_names = {tool.name for tool in self.catalog.tools}
        for spec in self.tool_registry.list_specs():
            if spec.name in catalog_tool_names:
                continue
            if not spec.model_facing:
                continue
            item: dict[str, object] = {
                "name": spec.name,
                "source": "builtin",
                "category": _tool_category(spec.name),
                "risk_level": spec.risk_level.value,
                "required_capabilities": sorted(spec.required_capabilities),
                "enabled": True,
                "health": "healthy",
                "description": spec.description,
            }
            if spec.sandbox_required:
                groups["sandbox"].append(item)
            else:
                groups["builtin"].append(item)

        source_by_id = {source.id: source for source in self.catalog.sources}
        registered_tool_names = {spec.name for spec in self.tool_registry.list_specs()}
        for tool in self.catalog.tools:
            source = source_by_id.get(tool.source_id)
            is_mcp = _is_mcp_source(source)
            enabled = tool.name in registered_tool_names
            item = {
                "name": tool.name,
                "source": tool.source_id,
                "category": "mcp" if is_mcp else "extension",
                "risk_level": tool.risk_level.value,
                "required_capabilities": sorted(tool.required_capabilities),
                "enabled": enabled,
                "health": (
                    source.health.status.value if source is not None else "unknown"
                ),
                "description": tool.description,
            }
            if not enabled:
                item["status"] = "execution_backend_not_ready"
            groups["mcp" if is_mcp else "extension"].append(item)
        return groups

    def skills(self) -> list[dict[str, object]]:
        return [
            {
                "id": skill.id,
                "name": skill.id,
                "version": skill.version,
                "source_id": skill.source_id,
                "risk_level": skill.risk_level.value,
                "status": "available",
                "stageable": True,
                "requested_tools": list(skill.requested_tools),
                "granted_tools": [],
                "denied_tools": [],
                "required_capabilities": sorted(skill.required_capabilities),
            }
            for skill in self.catalog.skills
        ]

    def mcp_servers(self) -> list[dict[str, object]]:
        tool_counts: dict[str, int] = {}
        for tool in self.catalog.tools:
            tool_counts[tool.source_id] = tool_counts.get(tool.source_id, 0) + 1
        return [
            {
                "id": source.id,
                "type": source.type.value,
                "trust": source.trust.value,
                "status": source.health.status.value,
                "detail": source.health.detail,
                "checked_at": source.health.checked_at.isoformat(),
                "tools": tool_counts.get(source.id, 0),
            }
            for source in self.catalog.sources
            if _is_mcp_source(source)
        ]


def _tool_category(tool_name: str) -> str:
    if tool_name in {"ReadFile", "WriteFile", "EditFile", "Glob", "Grep"}:
        return "repository"
    if tool_name == "Bash":
        return "sandbox"
    if tool_name.startswith("repo."):
        return "repository"
    if tool_name.startswith("shell."):
        return "sandbox"
    if tool_name.startswith("artifact."):
        return "artifact"
    return "builtin"


def _is_mcp_source(source: ExtensionSourceSnapshot | None) -> bool:
    return source is not None and source.type in {
        ExtensionSourceType.MCP_STDIO,
        ExtensionSourceType.MCP_STREAMABLE_HTTP,
    }
