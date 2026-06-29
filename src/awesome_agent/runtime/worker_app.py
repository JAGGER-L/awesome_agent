from __future__ import annotations

import signal
from collections.abc import Callable
from datetime import timedelta
from types import FrameType
from typing import Any

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.observability.facade import ObservabilityFacade
from awesome_agent.observability.otel import OTelConfig, configure_otel
from awesome_agent.observability.repository import PostgresObservabilityRepository
from awesome_agent.persistence.approvals import PostgresApprovalRepository
from awesome_agent.persistence.artifacts import PostgresArtifactMetadataRepository
from awesome_agent.persistence.budget import PostgresBudgetRepository
from awesome_agent.persistence.checkpoints import checkpoint_saver
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.dispatch import PostgresRunDispatcher
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.persistence.team import PostgresTeamRepository
from awesome_agent.persistence.tool_invocations import PostgresToolInvocationRepository
from awesome_agent.persistence.validation import PostgresValidationRepository
from awesome_agent.persistence.worker_heartbeats import (
    PostgresWorkerHeartbeatRepository,
)
from awesome_agent.providers.factory import ModelProviderFactory
from awesome_agent.runtime.budget import BudgetPolicy
from awesome_agent.runtime.context import ContextManager, DeterministicSummaryProvider
from awesome_agent.runtime.modifying_graph import ModifyingCodingGraph
from awesome_agent.runtime.probe_graph import RuntimeProbeGraph
from awesome_agent.runtime.readonly_graph import ReadOnlyCodingGraph
from awesome_agent.runtime.team_graph import TeamCodingGraph
from awesome_agent.runtime.team_leader_graph import TeamLeaderGraph
from awesome_agent.runtime.team_role_graph import TeamRoleGraph
from awesome_agent.runtime.team_verifier_graph import TeamVerifierGraph
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig
from awesome_agent.settings import Settings


async def run_worker(*, once: bool = False, settings: Settings | None = None) -> bool:
    configured = settings or Settings()
    engine = create_engine(configured.database_url)
    sessions = create_session_factory(engine)
    providers = ModelProviderFactory(configured)
    artifact_store = LocalArtifactStore(configured.artifact_root)
    artifact_repository = PostgresArtifactMetadataRepository(sessions)
    team_repository = PostgresTeamRepository(sessions)
    observability_repository = PostgresObservabilityRepository(sessions)
    otel_provider = configure_otel(OTelConfig(process_kind="worker"))
    observability = ObservabilityFacade(
        repository=observability_repository,
        tracer=otel_provider.get_tracer("awesome_agent.worker"),
    )
    budget_repository = PostgresBudgetRepository(sessions)
    budget_policy = BudgetPolicy(
        soft_context_tokens=configured.soft_context_tokens,
        hard_context_tokens=configured.hard_context_tokens,
        recent_context_tokens=configured.recent_context_tokens,
        max_total_tokens_per_run=configured.max_total_tokens_per_run,
        max_reasoning_tokens_per_run=configured.max_reasoning_tokens_per_run,
        max_active_seconds_per_run=configured.max_active_seconds_per_run,
    )
    context_manager = ContextManager(
        summary_provider=DeterministicSummaryProvider(),
        artifact_store=artifact_store,
        artifact_repository=artifact_repository,
    )
    async with checkpoint_saver(configured.checkpoint_database_url) as saver:
        await saver.setup()
        coding_graph = (
            ReadOnlyCodingGraph(
                saver,
                provider_resolver=providers.create,
                max_model_turns=configured.max_model_turns,
                max_tool_calls=configured.max_tool_calls_per_run,
                max_parallel_tools=configured.max_parallel_read_tools,
                recursion_limit=configured.agent_graph_recursion_limit,
                no_progress_turns=configured.no_progress_turns,
                context_manager=context_manager,
                budget_repository=budget_repository,
                budget_policy=budget_policy,
                observability=observability,
            )
            if providers.coding_available
            else None
        )
        modifying_graph = (
            ModifyingCodingGraph(
                saver,
                provider_resolver=providers.create,
                artifact_store=artifact_store,
                artifact_repository=artifact_repository,
                tool_repository=PostgresToolInvocationRepository(sessions),
                approval_repository=PostgresApprovalRepository(sessions),
                validation_repository=PostgresValidationRepository(sessions),
                approval_default_expiry=timedelta(
                    seconds=configured.approval_default_expiry_seconds
                ),
                max_model_turns=configured.max_model_turns,
                max_tool_calls=configured.max_tool_calls_per_run,
                recursion_limit=configured.agent_graph_recursion_limit,
                no_progress_turns=configured.no_progress_turns,
                context_manager=context_manager,
                budget_repository=budget_repository,
                budget_policy=budget_policy,
                observability=observability,
            )
            if providers.coding_available
            else None
        )
        worker = DurableWorker(
            dispatcher=PostgresRunDispatcher(sessions),
            repository=PostgresRuntimeRepository(sessions),
            probe_graph=RuntimeProbeGraph(saver),
            coding_graph=coding_graph,
            modifying_graph=modifying_graph,
            team_graph=(
                TeamCodingGraph(
                    saver,
                    model_resolver=RoleModelResolver.from_settings(configured),
                    provider_resolver=providers.create,
                    validation_repository=PostgresValidationRepository(sessions),
                    tool_repository=PostgresToolInvocationRepository(sessions),
                    budget_repository=budget_repository,
                    budget_policy=budget_policy,
                )
                if providers.coding_available
                else None
            ),
            team_leader_graph=(
                TeamLeaderGraph(
                    team_repository=team_repository,
                    provider_resolver=providers.create,
                    model_resolver=RoleModelResolver.from_settings(configured),
                    artifact_store=artifact_store,
                    artifact_repository=artifact_repository,
                    budget_repository=budget_repository,
                    budget_policy=budget_policy,
                )
                if providers.coding_available
                else None
            ),
            team_role_graph=TeamRoleGraph(
                team_repository=team_repository,
                provider_resolver=providers.create
                if providers.coding_available
                else None,
                artifact_store=artifact_store,
                artifact_repository=artifact_repository,
                budget_repository=budget_repository,
                budget_policy=budget_policy,
            ),
            team_verifier_graph=TeamVerifierGraph(
                team_repository=team_repository,
                provider_resolver=providers.create
                if providers.coding_available
                else None,
                artifact_store=artifact_store,
                artifact_repository=artifact_repository,
                budget_repository=budget_repository,
                budget_policy=budget_policy,
            ),
            config=WorkerConfig(
                lease_duration=timedelta(seconds=configured.lease_duration_seconds),
                heartbeat_interval=timedelta(
                    seconds=configured.heartbeat_interval_seconds
                ),
                poll_interval=configured.worker_poll_interval_seconds,
                recovery_interval=configured.worker_recovery_interval_seconds,
                shutdown_grace=configured.worker_shutdown_grace_seconds,
                retry_delay=timedelta(seconds=configured.worker_retry_delay_seconds),
                max_attempts=configured.max_claim_attempts,
            ),
            observability=observability,
            observability_repository=observability_repository,
            heartbeat_repository=PostgresWorkerHeartbeatRepository(sessions),
            budget_repository=budget_repository,
            team_repository=team_repository,
        )
        restore = _install_signal_handlers(worker)
        try:
            if once:
                await worker.dispatcher.recover_expired(
                    max_attempts=worker.config.max_attempts
                )
                await worker.dispatcher.expire_pending_approvals()
                return await worker.run_once()
            await worker.run_forever()
            return True
        finally:
            await worker.mark_stopping()
            restore()
            await engine.dispose()


def _install_signal_handlers(worker: DurableWorker) -> Callable[[], None]:
    previous: dict[signal.Signals, Any] = {}

    def request_stop(_: int, __: FrameType | None) -> None:
        worker.request_stop()

    supported = [signal.SIGINT, signal.SIGTERM]
    for current in supported:
        previous[current] = signal.getsignal(current)
        signal.signal(current, request_stop)

    def restore() -> None:
        for current, handler in previous.items():
            signal.signal(current, handler)

    return restore
