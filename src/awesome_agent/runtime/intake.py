from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    DispatchStatus,
    EventType,
    ExecutionKind,
    IntakeReservationStatus,
    RunIntent,
    RunStatus,
    WorkspaceState,
)
from awesome_agent.domain.models import (
    Agent,
    IntakeReservation,
    Run,
    RuntimeEvent,
)
from awesome_agent.repositories.git import (
    InvalidRepository,
    require_primary_clean_repository,
)
from awesome_agent.repositories.policy import ensure_allowed_path
from awesome_agent.repositories.registry import RepositoryRegistry
from awesome_agent.repositories.reservations import IntakeReservationStore
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.graphs import (
    READ_ONLY_CODING_GRAPH,
    READ_ONLY_CODING_VERSION,
)
from awesome_agent.runtime.repository import RuntimeRepository


class RunIntakeError(RuntimeError):
    pass


class RunIntakeService:
    def __init__(
        self,
        *,
        registry: RepositoryRegistry,
        reservations: IntakeReservationStore,
        runtime: RuntimeRepository,
        events: EventStream,
        worktrees: ManagedRunWorktreeManager,
        allowed_roots: list[Path],
        model_resolver: RoleModelResolver,
    ) -> None:
        self.registry = registry
        self.reservations = reservations
        self.runtime = runtime
        self.events = events
        self.worktrees = worktrees
        self.allowed_roots = allowed_roots
        self.model_resolver = model_resolver

    async def create_run(
        self,
        *,
        repository_id: UUID,
        goal: str,
        intent: RunIntent,
        execution_kind: ExecutionKind = ExecutionKind.CODING,
        graph_name: str | None = None,
        graph_version: int | None = None,
    ) -> Run:
        if (
            execution_kind is ExecutionKind.CODING
            and intent is RunIntent.READ_ONLY
            and graph_name is None
        ):
            graph_name = READ_ONLY_CODING_GRAPH
            graph_version = READ_ONLY_CODING_VERSION
        await self.reconcile_incomplete()
        repository = await self.registry.get(repository_id)
        if not repository.enabled:
            raise RunIntakeError("Repository is disabled.")
        root = ensure_allowed_path(repository.root, self.allowed_roots)
        try:
            snapshot = await require_primary_clean_repository(root)
        except InvalidRepository as error:
            raise RunIntakeError(str(error)) from error
        if snapshot.git_common_dir != repository.git_common_dir:
            raise RunIntakeError("Repository Git identity no longer matches.")

        run_id = uuid4()
        branch = self.worktrees.branch_for(run_id)
        workspace = self.worktrees.target_for(repository.id, run_id)
        reservation = IntakeReservation(
            run_id=run_id,
            repository_id=repository.id,
            base_commit=snapshot.head_commit,
            intent=intent,
            workspace_path=workspace,
            integration_branch=branch,
        )
        await self.reservations.create(reservation)
        try:
            await self.worktrees.provision(
                repository=repository.root,
                repository_id=repository.id,
                run_id=run_id,
                base_commit=snapshot.head_commit,
            )
            run, leader, initial_events = self._build_public_run(
                reservation=reservation,
                goal=goal,
                execution_kind=execution_kind,
                graph_name=graph_name,
                graph_version=graph_version,
            )
            await self.runtime.publish_intake(
                run=run,
                leader=leader,
                events=initial_events,
                reservation_id=reservation.id,
            )
        except Exception as error:
            failed = reservation.model_copy(
                update={
                    "status": IntakeReservationStatus.ROLLBACK_REQUIRED,
                    "error": str(error),
                    "updated_at": datetime.now(UTC),
                }
            )
            await self.reservations.update(failed)
            await self._rollback(failed, repository.root)
            raise

        for event in initial_events:
            await self.events.publish(event)
        return run

    async def reconcile_incomplete(self) -> None:
        for reservation in await self.reservations.list_incomplete():
            repository = await self.registry.get(reservation.repository_id)
            await self._rollback(reservation, repository.root)

    async def _rollback(
        self,
        reservation: IntakeReservation,
        repository_root: Path,
    ) -> None:
        rolled_back = await self.worktrees.rollback(
            repository=repository_root,
            repository_id=reservation.repository_id,
            run_id=reservation.run_id,
            base_commit=reservation.base_commit,
        )
        if rolled_back:
            await self.reservations.update(
                reservation.model_copy(
                    update={
                        "status": IntakeReservationStatus.ROLLED_BACK,
                        "updated_at": datetime.now(UTC),
                    }
                )
            )

    def _build_public_run(
        self,
        *,
        reservation: IntakeReservation,
        goal: str,
        execution_kind: ExecutionKind,
        graph_name: str | None,
        graph_version: int | None,
    ) -> tuple[Run, Agent, list[RuntimeEvent]]:
        run = Run(
            id=reservation.run_id,
            goal=goal,
            status=RunStatus.CREATED,
            repository_id=reservation.repository_id,
            base_commit=reservation.base_commit,
            intent=reservation.intent,
            execution_kind=execution_kind,
            graph_name=graph_name,
            graph_version=graph_version,
            dispatch_status=DispatchStatus.QUEUED,
            workspace_path=reservation.workspace_path,
            integration_branch=reservation.integration_branch,
            workspace_state=WorkspaceState.READY,
            graph_thread_id=f"run:{reservation.run_id}",
        )
        leader = Agent(
            run_id=run.id,
            kind=AgentKind.LEADER,
            profile="leader",
            model=self.model_resolver.resolve(
                kind=AgentKind.LEADER,
                profile="leader",
            ),
            status=AgentStatus.READY,
        )
        events = [
            RuntimeEvent(
                run_id=run.id,
                sequence=1,
                event_type=EventType.RUN_CREATED,
                payload={
                    "goal": goal,
                    "repository_id": str(run.repository_id),
                    "base_commit": run.base_commit,
                    "intent": run.intent.value,
                    "dispatch_status": run.dispatch_status.value,
                    "integration_branch": run.integration_branch,
                },
            ),
            RuntimeEvent(
                run_id=run.id,
                sequence=2,
                event_type=EventType.AGENT_CREATED,
                payload={
                    "agent_id": str(leader.id),
                    "kind": leader.kind.value,
                    "profile": leader.profile,
                    "model": leader.model,
                },
                agent_id=leader.id,
            ),
        ]
        return run, leader, events
