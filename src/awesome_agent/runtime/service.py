from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from awesome_agent.artifacts.store import ArtifactMetadata, LocalArtifactStore
from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    EventType,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run, RuntimeEvent, TodoItem
from awesome_agent.runtime.events import EventStream


class RuntimeService:
    def __init__(
        self,
        *,
        events: EventStream,
        artifacts: LocalArtifactStore,
    ) -> None:
        self.events = events
        self.artifacts = artifacts
        self._runs: dict[UUID, Run] = {}
        self._agents: dict[UUID, list[Agent]] = defaultdict(list)
        self._todos: dict[UUID, list[TodoItem]] = defaultdict(list)
        self._sequences: dict[UUID, int] = defaultdict(int)

    async def create_run(self, goal: str) -> Run:
        run = Run(goal=goal, status=RunStatus.RUNNING)
        leader = Agent(
            run_id=run.id,
            kind=AgentKind.LEADER,
            profile="leader",
            status=AgentStatus.READY,
        )
        self._runs[run.id] = run
        self._agents[run.id].append(leader)
        await self._emit(run.id, EventType.RUN_CREATED, {"goal": goal})
        await self._emit(
            run.id,
            EventType.AGENT_CREATED,
            {
                "agent_id": str(leader.id),
                "kind": leader.kind.value,
                "profile": leader.profile,
            },
            agent_id=leader.id,
        )
        return run

    def get_run(self, run_id: UUID) -> Run:
        return self._runs[run_id]

    async def cancel_run(self, run_id: UUID) -> Run:
        run = self._runs[run_id].model_copy(update={"status": RunStatus.CANCELLED})
        self._runs[run_id] = run
        await self._emit(
            run_id,
            EventType.RUN_STATUS_CHANGED,
            {"status": run.status.value},
        )
        return run

    async def resume_run(self, run_id: UUID) -> Run:
        current = self._runs[run_id]
        if current.status is RunStatus.COMPLETED:
            raise ValueError("Completed runs cannot be resumed.")
        run = current.model_copy(update={"status": RunStatus.RUNNING})
        self._runs[run_id] = run
        await self._emit(
            run_id,
            EventType.RUN_STATUS_CHANGED,
            {"status": run.status.value},
        )
        return run

    async def decide_approval(
        self,
        run_id: UUID,
        *,
        approval_id: UUID,
        approved: bool,
    ) -> RuntimeEvent:
        self.get_run(run_id)
        return await self._emit(
            run_id,
            EventType.APPROVAL_DECIDED,
            {
                "approval_id": str(approval_id),
                "approved": approved,
            },
        )

    def list_agents(self, run_id: UUID) -> list[Agent]:
        return list(self._agents[run_id])

    def list_todos(self, run_id: UUID) -> list[TodoItem]:
        return list(self._todos[run_id])

    def list_artifacts(self, run_id: UUID) -> list[ArtifactMetadata]:
        return self.artifacts.list_for_run(run_id)

    async def _emit(
        self,
        run_id: UUID,
        event_type: EventType,
        payload: dict[str, object],
        *,
        agent_id: UUID | None = None,
    ) -> RuntimeEvent:
        self._sequences[run_id] += 1
        event = RuntimeEvent(
            run_id=run_id,
            sequence=self._sequences[run_id],
            event_type=event_type,
            payload=payload,
            agent_id=agent_id,
        )
        await self.events.publish(event)
        return event
