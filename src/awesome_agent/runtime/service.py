from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from uuid import UUID

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.repository import (
    ArtifactMetadataRepository,
    InMemoryArtifactMetadataRepository,
)
from awesome_agent.artifacts.store import ArtifactMetadata, LocalArtifactStore
from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    EventType,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run, RuntimeEvent, TodoItem
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.repository import RuntimeRepository


class RuntimeService:
    def __init__(
        self,
        *,
        repository: RuntimeRepository,
        events: EventStream,
        artifacts: LocalArtifactStore,
        model_resolver: RoleModelResolver,
        artifact_repository: ArtifactMetadataRepository | None = None,
        event_poll_interval: float = 0.5,
    ) -> None:
        self.repository = repository
        self.events = events
        self.artifacts = artifacts
        self.artifact_repository = (
            artifact_repository or InMemoryArtifactMetadataRepository()
        )
        self.model_resolver = model_resolver
        self.event_poll_interval = event_poll_interval

    async def create_run(self, goal: str) -> Run:
        run = Run(goal=goal, status=RunStatus.RUNNING)
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
        await self.repository.create_run(run, leader)
        await self._emit(run.id, EventType.RUN_CREATED, {"goal": goal})
        await self._emit(
            run.id,
            EventType.AGENT_CREATED,
            {
                "agent_id": str(leader.id),
                "kind": leader.kind.value,
                "profile": leader.profile,
                "model": leader.model,
            },
            agent_id=leader.id,
        )
        return run

    async def get_run(self, run_id: UUID) -> Run:
        return await self.repository.get_run(run_id)

    async def cancel_run(self, run_id: UUID) -> Run:
        run, event = await self.repository.cancel_run(run_id)
        if event is not None:
            await self.events.publish(event)
        return run

    async def resume_run(self, run_id: UUID) -> Run:
        current = await self.repository.get_run(run_id)
        if current.repository_id is not None:
            raise ValueError(
                "Durable Run resume is unavailable before dispatch recovery."
            )
        if current.status in {
            RunStatus.COMPLETED,
            RunStatus.RECOVERY_REQUIRED,
        }:
            raise ValueError("Run cannot be resumed.")
        run = current.model_copy(update={"status": RunStatus.RUNNING})
        await self.repository.update_run(run)
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
        await self.get_run(run_id)
        return await self._emit(
            run_id,
            EventType.APPROVAL_DECIDED,
            {
                "approval_id": str(approval_id),
                "approved": approved,
            },
        )

    async def list_agents(self, run_id: UUID) -> list[Agent]:
        return await self.repository.list_agents(run_id)

    async def list_todos(self, run_id: UUID) -> list[TodoItem]:
        return await self.repository.list_todos(run_id)

    async def list_events(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> list[RuntimeEvent]:
        return await self.repository.list_events(
            run_id,
            after_sequence=after_sequence,
        )

    async def stream_events(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> AsyncGenerator[RuntimeEvent]:
        history = await self.list_events(run_id, after_sequence=after_sequence)
        cursor = after_sequence
        for event in history:
            cursor = event.sequence
            yield event
        while True:
            pending = await self.list_events(run_id, after_sequence=cursor)
            if pending:
                for event in pending:
                    cursor = event.sequence
                    yield event
                continue
            await asyncio.sleep(self.event_poll_interval)

    async def write_artifact(
        self,
        *,
        run_id: UUID,
        artifact_type: str,
        filename: str,
        content: bytes,
        mime_type: str,
        summary: str = "",
        agent_id: UUID | None = None,
    ) -> ArtifactMetadata:
        metadata = self.artifacts.write(
            run_id=run_id,
            artifact_type=artifact_type,
            filename=filename,
            content=content,
            mime_type=mime_type,
            summary=summary,
            agent_id=agent_id,
        )
        return await self.artifact_repository.record(metadata)

    async def get_artifact(self, artifact_id: UUID) -> ArtifactMetadata:
        return await self.artifact_repository.get(artifact_id)

    async def list_artifacts(self, run_id: UUID) -> list[ArtifactMetadata]:
        return await self.artifact_repository.list_for_run(run_id)

    async def _emit(
        self,
        run_id: UUID,
        event_type: EventType,
        payload: dict[str, object],
        *,
        agent_id: UUID | None = None,
    ) -> RuntimeEvent:
        event = await self.repository.append_event(
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            agent_id=agent_id,
        )
        await self.events.publish(event)
        return event
