from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from awesome_agent.conversation.intake import ConversationRunIntakeService
from awesome_agent.conversation.service import ConversationService
from awesome_agent.domain.enums import ExecutionOrigin
from awesome_agent.domain.models import Run
from awesome_agent.modeling.provider import ModelProvider
from awesome_agent.persistence.budget import InMemoryBudgetRepository
from awesome_agent.persistence.local_approvals import LocalApprovalRepository
from awesome_agent.persistence.local_artifacts import LocalArtifactMetadataRepository
from awesome_agent.persistence.local_conversations import LocalConversationRepository
from awesome_agent.persistence.local_dispatch import LocalRunDispatcher
from awesome_agent.persistence.local_runtime import LocalRuntimeRepository
from awesome_agent.providers.factory import ModelProviderFactory
from awesome_agent.runtime.conversation_graph import ConversationGraph
from awesome_agent.runtime.dispatch import IncompatibleGraphError
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.probe_graph import RuntimeProbeState
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig
from awesome_agent.sandbox.factory import create_sandbox
from awesome_agent.settings import Settings
from awesome_agent.surfaces.local_worker_pump import LocalWorkerPump
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
    ) -> None:
        self.settings = settings
        self.default_model = default_model or settings.leader_model
        database_path = state_path or settings.local_state_dir / "awesome-agent.db"

        self.conversations = LocalConversationRepository(database_path)
        self.runtime = LocalRuntimeRepository(database_path)
        self.artifacts = LocalArtifactMetadataRepository(database_path)
        self.approvals = LocalApprovalRepository(database_path)
        self.dispatcher = LocalRunDispatcher(
            self.runtime,
            approval_repository=self.approvals,
        )
        self.events = EventStream()

        factory = provider_factory or ModelProviderFactory(settings).create
        sandbox = create_sandbox(
            origin=ExecutionOrigin.CLI,
            settings=settings,
            profile="local-cli",
        )
        self.tool_registry = build_modifying_registry(sandbox=sandbox)
        self.tool_executor = build_modifying_executor(self.tool_registry)

        self.conversation_intake = ConversationRunIntakeService(
            conversations=self.conversations,
            runtime=self.runtime,
            events=self.events,
            default_model=self.default_model,
        )
        self.conversation_graph = ConversationGraph(
            conversations=self.conversations,
            runtime=self.runtime,
            provider_factory=factory,
            default_model=self.default_model,
            tool_executor=self.tool_executor,
            tool_registry=self.tool_registry,
        )
        self.conversation_service = ConversationService(
            repository=self.conversations,
            runtime_repository=self.runtime,
            conversation_run_intake=self.conversation_intake,
            default_model=self.default_model,
            event_poll_interval=0,
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
        self.approvals.close()
