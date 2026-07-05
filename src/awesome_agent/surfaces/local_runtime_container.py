from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from awesome_agent.attachments.service import AttachmentService
from awesome_agent.attachments.store import AttachmentContentStore
from awesome_agent.conversation.intake import ConversationRunIntakeService
from awesome_agent.conversation.service import ConversationService
from awesome_agent.domain.enums import ExecutionOrigin
from awesome_agent.domain.models import Run
from awesome_agent.extensions.catalog_store import LocalExtensionCatalogStore
from awesome_agent.extensions.mcp import (
    register_mcp_stdio_tools,
    register_mcp_streamable_http_tools,
)
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionSourceConfig,
    ExtensionSourceType,
)
from awesome_agent.extensions.runtime_catalog import build_startup_extension_runtime
from awesome_agent.memory.builtin import BuiltinMemoryStore
from awesome_agent.memory.external import NoopMemoryProvider
from awesome_agent.memory.policy import MemoryPolicy
from awesome_agent.memory.service import MemoryService
from awesome_agent.modeling.execution import (
    InProcessModelExecutionBackend,
    ModelExecutionService,
)
from awesome_agent.modeling.process_backend import ProcessModelExecutionBackend
from awesome_agent.modeling.provider import ModelProvider
from awesome_agent.persistence.budget import InMemoryBudgetRepository
from awesome_agent.persistence.local_approvals import LocalApprovalRepository
from awesome_agent.persistence.local_artifacts import LocalArtifactMetadataRepository
from awesome_agent.persistence.local_attachments import LocalAttachmentRepository
from awesome_agent.persistence.local_conversations import LocalConversationRepository
from awesome_agent.persistence.local_cwd_context import (
    LocalCwdContextSnapshotRepository,
)
from awesome_agent.persistence.local_dispatch import LocalRunDispatcher
from awesome_agent.persistence.local_runtime import LocalRuntimeRepository
from awesome_agent.providers.factory import ModelProviderFactory
from awesome_agent.runtime.conversation_graph import ConversationGraph
from awesome_agent.runtime.cwd_context import CwdContextService
from awesome_agent.runtime.dispatch import IncompatibleGraphError
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.probe_graph import RuntimeProbeState
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig
from awesome_agent.sandbox.factory import create_sandbox
from awesome_agent.settings import Settings
from awesome_agent.surfaces.local_worker_pump import LocalWorkerPump
from awesome_agent.tools.attachments import register_attachment_tools
from awesome_agent.tools.memory import register_memory_tools
from awesome_agent.tools.models import ToolInvocation, ToolResult, ToolSpec
from awesome_agent.tools.registry import ProgressCallback, ToolRegistry
from awesome_agent.tools.repository import (
    build_modifying_executor,
    build_modifying_registry,
)


class _UnsupportedLocalProbeGraph:
    async def execute(self, run: Run) -> tuple[RuntimeProbeState, bool]:
        raise IncompatibleGraphError(
            "Embedded local runtime does not execute diagnostic runtime probes."
        )


class LocalRuntimeContainer:
    def __init__(
        self,
        *,
        settings: Settings,
        provider_factory: Callable[[str], ModelProvider] | None = None,
        default_model: str | None = None,
        state_path: Path | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.settings = settings
        self.default_model = default_model or settings.leader_model
        database_path = state_path or settings.local_state_dir / "awesome-agent.db"
        self.project_root = (project_root or Path.cwd()).resolve()

        self.conversations = LocalConversationRepository(database_path)
        self.runtime = LocalRuntimeRepository(database_path)
        self.artifacts = LocalArtifactMetadataRepository(database_path)
        self.attachments = LocalAttachmentRepository(database_path)
        self.cwd_context_snapshots = LocalCwdContextSnapshotRepository(database_path)
        self.approvals = LocalApprovalRepository(database_path)
        self.dispatcher = LocalRunDispatcher(
            self.runtime,
            approval_repository=self.approvals,
        )
        self.events = EventStream()
        self.extension_runtime = build_startup_extension_runtime(self.project_root)
        self.extension_catalog_store = LocalExtensionCatalogStore(database_path)
        self.extension_catalog_store.put(self.extension_runtime.catalog, active=True)
        self.extension_source_configs = {
            source.id: source for source in self.extension_runtime.source_configs
        }
        self.extension_catalog_error = self.extension_runtime.error

        factory = provider_factory or ModelProviderFactory(settings).create
        model_execution_service = (
            ModelExecutionService(InProcessModelExecutionBackend(factory))
            if provider_factory is not None
            else ModelExecutionService(
                ProcessModelExecutionBackend(
                    python_executable=sys.executable,
                    first_event_timeout_seconds=(
                        settings.model_first_event_timeout_seconds
                    ),
                    idle_timeout_seconds=settings.model_idle_timeout_seconds,
                    total_timeout_seconds=settings.model_total_timeout_seconds,
                    shutdown_grace_seconds=(
                        settings.model_process_shutdown_grace_seconds
                    ),
                )
            )
        )
        sandbox = create_sandbox(
            origin=ExecutionOrigin.CLI,
            settings=settings,
            profile="local-cli",
        )
        self.memory_service = MemoryService(
            builtin=BuiltinMemoryStore(
                root=settings.local_state_dir / "memory",
                policy=MemoryPolicy(),
            ),
            provider=NoopMemoryProvider(),
            builtin_enabled=settings.builtin_memory_enabled,
            provider_enabled=settings.mem0_enabled,
        )
        self.attachment_service = AttachmentService(
            repository=self.attachments,
            store=AttachmentContentStore(settings.local_state_dir / "attachments"),
        )
        self.cwd_context_service = CwdContextService(
            repository=self.cwd_context_snapshots,
        )
        self.tool_registry = build_modifying_registry(sandbox=sandbox)
        register_memory_tools(self.tool_registry, self.memory_service)
        register_attachment_tools(self.tool_registry, self.attachment_service)
        _register_extension_tools(
            self.tool_registry,
            source_configs=self.extension_source_configs,
            catalog=self.extension_runtime.catalog,
        )
        self.tool_executor = build_modifying_executor(self.tool_registry)

        self.conversation_intake = ConversationRunIntakeService(
            conversations=self.conversations,
            runtime=self.runtime,
            events=self.events,
            default_model=self.default_model,
            extension_catalog_version=self.extension_runtime.catalog.version,
            attachment_service=self.attachment_service,
        )
        self.conversation_graph = ConversationGraph(
            conversations=self.conversations,
            runtime=self.runtime,
            provider_factory=factory,
            default_model=self.default_model,
            tool_executor=self.tool_executor,
            tool_registry=self.tool_registry,
            extension_catalog_store=self.extension_catalog_store,
            memory_service=self.memory_service,
            attachment_service=self.attachment_service,
            cwd_context_service=self.cwd_context_service,
            model_execution_service=model_execution_service,
        )
        self.conversation_service = ConversationService(
            repository=self.conversations,
            runtime_repository=self.runtime,
            conversation_run_intake=self.conversation_intake,
            default_model=self.default_model,
            event_poll_interval=0,
            global_builtin_memory_enabled=settings.builtin_memory_enabled,
            global_provider_memory_enabled=settings.mem0_enabled,
        )
        self.worker = DurableWorker(
            dispatcher=self.dispatcher,
            repository=self.runtime,
            probe_graph=cast(Any, _UnsupportedLocalProbeGraph()),
            conversation_graph=self.conversation_graph,
            config=WorkerConfig(
                lease_duration=timedelta(seconds=60),
                heartbeat_interval=timedelta(seconds=15),
                poll_interval=0.01,
                recovery_interval=15,
                shutdown_grace=0.01,
                retry_delay=timedelta(seconds=0),
                max_attempts=3,
            ),
            budget_repository=InMemoryBudgetRepository(),
        )
        self.worker_pump = LocalWorkerPump(worker=self.worker, runtime=self.runtime)

    def close(self) -> None:
        self.conversations.close()
        self.runtime.close()
        self.artifacts.close()
        self.attachments.close()
        self.cwd_context_snapshots.close()
        self.approvals.close()
        self.extension_catalog_store.close()


async def _community_tool_not_ready(
    invocation: ToolInvocation,
    _: ProgressCallback | None,
) -> ToolResult:
    raise ValueError(f"execution_backend_not_ready: {invocation.tool_name}")


def _register_extension_tools(
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
