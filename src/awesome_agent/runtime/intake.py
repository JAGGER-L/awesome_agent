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
    RunMode,
    RunStatus,
    TodoStatus,
    WorkspaceState,
)
from awesome_agent.domain.models import (
    Agent,
    IntakeReservation,
    Run,
    RuntimeEvent,
    TodoItem,
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
    MODIFYING_CODING_GRAPH,
    MODIFYING_CODING_VERSION,
    READ_ONLY_CODING_GRAPH,
    READ_ONLY_CODING_VERSION,
    TEAM_CODING_GRAPH,
    TEAM_CODING_VERSION,
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
        mode: RunMode = RunMode.SOLO,
        execution_kind: ExecutionKind = ExecutionKind.CODING,
        graph_name: str | None = None,
        graph_version: int | None = None,
    ) -> Run:
        if (
            mode is RunMode.TEAM
            and execution_kind is ExecutionKind.CODING
            and graph_name is None
        ):
            graph_name = TEAM_CODING_GRAPH
            graph_version = TEAM_CODING_VERSION
        if (
            mode is RunMode.SOLO
            and execution_kind is ExecutionKind.CODING
            and intent is RunIntent.READ_ONLY
            and graph_name is None
        ):
            graph_name = READ_ONLY_CODING_GRAPH
            graph_version = READ_ONLY_CODING_VERSION
        if (
            mode is RunMode.SOLO
            and execution_kind is ExecutionKind.CODING
            and intent is RunIntent.MODIFYING
            and graph_name is None
        ):
            graph_name = MODIFYING_CODING_GRAPH
            graph_version = MODIFYING_CODING_VERSION
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
            run, leader, todo, initial_events = self._build_public_run(
                reservation=reservation,
                goal=goal,
                execution_kind=execution_kind,
                graph_name=graph_name,
                graph_version=graph_version,
                mode=mode,
            )
            await self.runtime.publish_intake(
                run=run,
                leader=leader,
                todo=todo,
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
        mode: RunMode,
    ) -> tuple[Run, Agent, TodoItem | None, list[RuntimeEvent]]:
        run = Run(
            id=reservation.run_id,
            goal=goal,
            mode=mode,
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
        todo = None
        if execution_kind is ExecutionKind.CODING and mode is RunMode.SOLO:
            if reservation.intent is RunIntent.READ_ONLY:
                todo = TodoItem(
                    run_id=run.id,
                    title="Inspect repository and answer the user goal",
                    description=goal,
                    status=TodoStatus.IN_PROGRESS,
                    primary_owner_id=leader.id,
                    acceptance_criteria=[
                        "Inspect repository evidence using read-only tools.",
                        "Return a bounded answer with remaining uncertainty.",
                    ],
                )
            elif reservation.intent is RunIntent.MODIFYING:
                todo = TodoItem(
                    run_id=run.id,
                    title="Modify the repository and report unvalidated changes",
                    description=goal,
                    status=TodoStatus.IN_PROGRESS,
                    primary_owner_id=leader.id,
                    acceptance_criteria=[
                        "Apply changes only in the managed Run worktree.",
                        "Inspect the final diff after the last write.",
                        "Report changed files, commands, and unverified work.",
                    ],
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
                    "mode": run.mode.value,
                    "dispatch_status": run.dispatch_status.value,
                    "integration_branch": run.integration_branch,
                },
                trace_id=run.id.hex,
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
                trace_id=run.id.hex,
            ),
        ]
        if todo is not None:
            events.append(
                RuntimeEvent(
                    run_id=run.id,
                    sequence=3,
                    event_type=EventType.TODO_CREATED,
                    payload={
                        "todo_id": str(todo.id),
                        "title": todo.title,
                        "status": todo.status.value,
                    },
                    agent_id=leader.id,
                    task_id=todo.id,
                    trace_id=run.id.hex,
                )
            )
        return run, leader, todo, events
