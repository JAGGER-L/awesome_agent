from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from awesome_agent.attachments.service import AttachmentService
from awesome_agent.domain.enums import ExecutionOrigin
from awesome_agent.extensions.mcp import (
    register_mcp_stdio_tools,
    register_mcp_streamable_http_tools,
)
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionSourceConfig,
    ExtensionSourceType,
)
from awesome_agent.extensions.runtime_catalog import (
    StartupExtensionRuntime,
    build_startup_extension_runtime,
)
from awesome_agent.memory.service import MemoryService
from awesome_agent.sandbox.factory import create_sandbox
from awesome_agent.settings import Settings
from awesome_agent.surfaces.capabilities import CapabilitySurfaceService
from awesome_agent.tools.attachments import register_attachment_tools
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.memory import register_memory_tools
from awesome_agent.tools.models import ToolInvocation, ToolResult, ToolSpec
from awesome_agent.tools.registry import ProgressCallback, ToolRegistry
from awesome_agent.tools.repository import (
    build_modifying_executor,
    build_modifying_registry,
)


@dataclass(frozen=True, slots=True)
class RuntimeToolAssembly:
    catalog: ExtensionCatalog
    source_configs: dict[str, ExtensionSourceConfig]
    tool_registry: ToolRegistry
    tool_executor: ToolExecutor
    capability_surface: CapabilitySurfaceService
    error: str | None = None


def assemble_runtime_tools(
    *,
    project_root: Path,
    settings: Settings,
    origin: ExecutionOrigin = ExecutionOrigin.CLI,
    sandbox_profile: str | None = None,
    memory_service: MemoryService | None = None,
    attachment_service: AttachmentService | None = None,
    catalog: ExtensionCatalog | None = None,
    source_configs: tuple[ExtensionSourceConfig, ...] | None = None,
    startup_runtime: StartupExtensionRuntime | None = None,
) -> RuntimeToolAssembly:
    runtime = startup_runtime
    if runtime is None and (catalog is None or source_configs is None):
        runtime = build_startup_extension_runtime(project_root)
    active_catalog = catalog or _require_startup_runtime(runtime).catalog
    active_source_configs = {
        source.id: source
        for source in (
            source_configs
            if source_configs is not None
            else _require_startup_runtime(runtime).source_configs
        )
    }
    sandbox = create_sandbox(
        origin=origin,
        settings=settings,
        profile=sandbox_profile,
    )
    registry = build_modifying_registry(sandbox=sandbox)
    if memory_service is not None:
        register_memory_tools(registry, memory_service)
    if attachment_service is not None:
        register_attachment_tools(registry, attachment_service)
    register_extension_tools(
        registry,
        source_configs=active_source_configs,
        catalog=active_catalog,
    )
    executor = build_modifying_executor(registry)
    return RuntimeToolAssembly(
        catalog=active_catalog,
        source_configs=active_source_configs,
        tool_registry=registry,
        tool_executor=executor,
        capability_surface=CapabilitySurfaceService(
            catalog=active_catalog,
            tool_registry=registry,
        ),
        error=runtime.error if runtime is not None else None,
    )


def _require_startup_runtime(
    runtime: StartupExtensionRuntime | None,
) -> StartupExtensionRuntime:
    if runtime is None:
        raise RuntimeError("Startup extension runtime is required.")
    return runtime


def register_extension_tools(
    registry: ToolRegistry,
    *,
    source_configs: dict[str, ExtensionSourceConfig],
    catalog: ExtensionCatalog,
) -> None:
    source_by_id = {source.id: source for source in catalog.sources}
    for source_id, config in source_configs.items():
        source = source_by_id.get(source_id)
        if source is None:
            continue
        if config.type is ExtensionSourceType.MCP_STDIO:
            register_mcp_stdio_tools(
                registry,
                config=config,
                catalog=catalog,
                exposed_tool_names={
                    tool.name for tool in catalog.tools if tool.source_id == source_id
                },
            )
        elif config.type is ExtensionSourceType.MCP_STREAMABLE_HTTP:
            register_mcp_streamable_http_tools(
                registry,
                config=config,
                catalog=catalog,
                exposed_tool_names={
                    tool.name for tool in catalog.tools if tool.source_id == source_id
                },
            )
    for tool in catalog.tools:
        source = source_by_id.get(tool.source_id)
        if (
            source is None
            or source.type is not ExtensionSourceType.COMMUNITY_TOOL_PACKAGE
        ):
            continue
        registry.register(
            ToolSpec(
                name=tool.name,
                description=tool.description,
                risk_level=tool.risk_level,
                required_capabilities=set(tool.required_capabilities),
                sandbox_required=True,
                input_schema=tool.input_schema,
            ),
            _community_tool_not_ready,
        )


async def _community_tool_not_ready(
    invocation: ToolInvocation,
    _: ProgressCallback | None,
) -> ToolResult:
    raise ValueError(f"execution_backend_not_ready: {invocation.tool_name}")
