from pathlib import Path
from uuid import UUID

import pytest

from awesome_agent.conversation.service import ConversationService
from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


@pytest.mark.asyncio
async def test_start_turn_creates_run_and_projects_runtime_events() -> None:
    conversations = InMemoryConversationRepository()
    thread = await conversations.create_thread(
        title="Chat",
        context_path="E:/project",
    )
    runtime = InMemoryRuntimeRepository()
    event_stream = EventStream()
    intake = FakeConversationRunIntake(runtime, event_stream)
    service = ConversationService(
        repository=conversations,
        runtime_repository=runtime,
        conversation_run_intake=intake,
        default_model="fake-model",
        event_poll_interval=0,
    )

    events = [
        event
        async for event in service.start_turn(
            thread_id=thread.id,
            content="hello",
            model=None,
            thinking=None,
            memory={},
            skill_ids=(),
        )
    ]

    assert intake.created == ["hello"]
    assert events[0].event.value == "turn.started"
    assert events[-1].event.value == "turn.completed"


class FakeConversationRunIntake:
    def __init__(
        self,
        runtime: InMemoryRuntimeRepository,
        events: EventStream,
    ) -> None:
        self.runtime = runtime
        self.events = events
        self.created: list[str] = []

    async def create_turn_run(
        self,
        *,
        thread_id: UUID,
        content: str,
        model: str | None,
        thinking: str | None,
        memory: dict[str, object],
        skill_ids: tuple[str, ...],
        attachment_ids: tuple[UUID, ...] = (),
    ) -> Run:
        self.created.append(content)
        run = Run(
            goal=content,
            intent=RunIntent.CONVERSATION,
            execution_kind=ExecutionKind.CONVERSATION,
            runtime_route=CONVERSATION_TURN_ROUTE,
            status=RunStatus.CREATED,
            dispatch_status=DispatchStatus.QUEUED,
            working_directory=Path("E:/project"),
        )
        leader = Agent(
            run_id=run.id,
            kind=AgentKind.LEADER,
            profile="leader",
            model=model or "fake-model",
            status=AgentStatus.READY,
        )
        await self.runtime.create_run(run, leader)
        created = await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.RUN_CREATED,
            payload={
                "thread_id": str(thread_id),
                "goal": content,
                "model": leader.model,
            },
            agent_id=leader.id,
        )
        completed = await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": RunStatus.COMPLETED.value,
                "dispatch_status": DispatchStatus.TERMINAL.value,
            },
            agent_id=leader.id,
        )
        await self.events.publish(created)
        await self.events.publish(completed)
        return run
