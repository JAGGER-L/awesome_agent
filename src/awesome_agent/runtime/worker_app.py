from __future__ import annotations

import signal
from collections.abc import Callable
from datetime import timedelta
from types import FrameType
from typing import Any

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import ExecutionOrigin
from awesome_agent.observability.facade import ObservabilityFacade
from awesome_agent.observability.otel import (
    OTelConfig,
    configure_otel,
    configure_otel_metrics,
)
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
from awesome_agent.runtime.team_recovery_policy import TeamRecoveryPolicy
from awesome_agent.runtime.team_role_graph import TeamRoleGraph
from awesome_agent.runtime.team_verifier_graph import TeamVerifierGraph
from awesome_agent.runtime.token_accounting import default_token_accountant
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig
from awesome_agent.sandbox.factory import create_sandbox
from awesome_agent.settings import Settings


async def run_worker(*, once: bool = False, settings: Settings | None = None) -> bool:
    configured = settings or Settings()
    engine = create_engine(configured.database_url)
    sessions = create_session_factory(engine)
    providers = ModelProviderFactory(configured)
    readonly_provider_resolver = providers.create_routed_resolver(
        runtime_route="solo-readonly",
        agent_role="leader",
    )
    modifying_provider_resolver = providers.create_routed_resolver(
        runtime_route="solo-modifying",
        agent_role="leader",
    )
    team_provider_resolver = providers.create_routed_resolver(
        runtime_route="team-coding-scoped",
        agent_role="leader",
    )
    team_leader_provider_resolver = providers.create_routed_resolver(
        runtime_route="team-coding",
        agent_role="leader",
    )
    team_role_provider_resolver = providers.create_routed_resolver(
        runtime_route="team-role",
        agent_role="teammate",
    )
    team_verifier_provider_resolver = providers.create_routed_resolver(
        runtime_route="team-verifier",
        agent_role="verifier",
    )
    artifact_store = LocalArtifactStore(configured.artifact_root)
    artifact_repository = PostgresArtifactMetadataRepository(sessions)
    team_repository = PostgresTeamRepository(sessions)
    observability_repository = PostgresObservabilityRepository(sessions)
    otel_config = OTelConfig(
        service_name=configured.otel_service_name,
        process_kind="worker",
        console_exporter=(configured.otel_console_exporter_enabled and not once),
        otlp_endpoint=configured.otel_otlp_endpoint,
    )
    otel_provider = (
        configure_otel(otel_config) if configured.observability_enabled else None
    )
    otel_metrics = (
        configure_otel_metrics(otel_config)
        if configured.observability_enabled
        else None
    )
    observability = ObservabilityFacade(
        repository=observability_repository,
        tracer=(
            otel_provider.get_tracer("awesome_agent.worker")
            if otel_provider is not None
            else None
        ),
        metric_recorder=otel_metrics,
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
    team_recovery_policy = TeamRecoveryPolicy(
        verifier_model_output_attempts=(configured.team_verifier_model_output_attempts),
        verifier_model_rejection_budget=(
            configured.team_verifier_model_rejection_budget
        ),
        verifier_external_retry_budget=(configured.team_verifier_external_retry_budget),
        verifier_plan_repair_budget=configured.team_verifier_plan_repair_budget,
        patch_conflict_rework_budget=configured.team_patch_conflict_rework_budget,
        model_output_rework_budget=configured.team_model_output_rework_budget,
        default_rework_budget=configured.team_default_rework_budget,
    )
    token_accountant = default_token_accountant()
    sandbox = create_sandbox(origin=ExecutionOrigin.API, settings=configured)
    context_manager = ContextManager(
        summary_provider=DeterministicSummaryProvider(),
        artifact_store=artifact_store,
        artifact_repository=artifact_repository,
        token_accountant=token_accountant,
    )
    async with checkpoint_saver(configured.checkpoint_database_url) as saver:
        await saver.setup()
        coding_graph = (
            ReadOnlyCodingGraph(
                saver,
                provider_resolver=readonly_provider_resolver,
                max_model_turns=configured.max_model_turns,
                max_tool_calls=configured.max_tool_calls_per_run,
                max_parallel_tools=configured.max_parallel_read_tools,
                recursion_limit=configured.agent_graph_recursion_limit,
                no_progress_turns=configured.no_progress_turns,
                context_manager=context_manager,
                budget_repository=budget_repository,
                budget_policy=budget_policy,
                observability=observability,
                token_accountant=token_accountant,
            )
            if providers.coding_available
            else None
        )
        modifying_graph = (
            ModifyingCodingGraph(
                saver,
                provider_resolver=modifying_provider_resolver,
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
                token_accountant=token_accountant,
                sandbox=sandbox,
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
                    provider_resolver=team_provider_resolver,
                    validation_repository=PostgresValidationRepository(sessions),
                    tool_repository=PostgresToolInvocationRepository(sessions),
                    budget_repository=budget_repository,
                    budget_policy=budget_policy,
                    token_accountant=token_accountant,
                )
                if providers.coding_available
                else None
            ),
            team_leader_graph=(
                TeamLeaderGraph(
                    team_repository=team_repository,
                    provider_resolver=team_leader_provider_resolver,
                    model_resolver=RoleModelResolver.from_settings(configured),
                    artifact_store=artifact_store,
                    artifact_repository=artifact_repository,
                    budget_repository=budget_repository,
                    budget_policy=budget_policy,
                    observability=observability,
                    team_recovery_policy=team_recovery_policy,
                    token_accountant=token_accountant,
                )
                if providers.coding_available
                else None
            ),
            team_role_graph=TeamRoleGraph(
                team_repository=team_repository,
                provider_resolver=team_role_provider_resolver
                if providers.coding_available
                else None,
                artifact_store=artifact_store,
                artifact_repository=artifact_repository,
                budget_repository=budget_repository,
                budget_policy=budget_policy,
                validation_repository=PostgresValidationRepository(sessions),
                observability=observability,
                token_accountant=token_accountant,
            ),
            team_verifier_graph=TeamVerifierGraph(
                team_repository=team_repository,
                provider_resolver=team_verifier_provider_resolver
                if providers.coding_available
                else None,
                artifact_store=artifact_store,
                artifact_repository=artifact_repository,
                budget_repository=budget_repository,
                budget_policy=budget_policy,
                observability=observability,
                team_recovery_policy=team_recovery_policy,
                token_accountant=token_accountant,
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
