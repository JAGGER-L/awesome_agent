from __future__ import annotations

from pathlib import Path
from uuid import UUID

from awesome_agent.conversation.repository import ConversationRepository
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
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE
from awesome_agent.runtime.repository import RuntimeRepository


class ConversationRunIntakeService:
    def __init__(
        self,
        *,
        conversations: ConversationRepository,
        runtime: RuntimeRepository,
        events: EventStream,
        default_model: str,
    ) -> None:
        self.conversations = conversations
        self.runtime = runtime
        self.events = events
        self.default_model = default_model

    async def create_turn_run(
        self,
        *,
        thread_id: UUID,
        content: str,
        model: str | None,
        thinking: str | None,
        memory: dict[str, object],
        skill_ids: tuple[str, ...],
    ) -> Run:
        thread = await self.conversations.get_thread(thread_id)
        if not thread.context_path:
            raise ValueError("Thread is missing a working directory context path.")

        selected_model = model or thread.default_model or self.default_model
        run = Run(
            goal=content,
            intent=RunIntent.CONVERSATION,
            execution_kind=ExecutionKind.CONVERSATION,
            runtime_route=CONVERSATION_TURN_ROUTE,
            status=RunStatus.CREATED,
            dispatch_status=DispatchStatus.QUEUED,
            working_directory=Path(thread.context_path),
        )
        run = run.model_copy(update={"graph_thread_id": f"conversation:{run.id}"})
        leader = Agent(
            run_id=run.id,
            kind=AgentKind.LEADER,
            profile="leader",
            model=selected_model,
            status=AgentStatus.READY,
        )

        await self.runtime.create_run(run, leader)
        created_event = await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.RUN_CREATED,
            payload={
                "thread_id": str(thread_id),
                "goal": content,
                "model": selected_model,
                "thinking": thinking,
                "memory": memory,
                "skill_ids": list(skill_ids),
                "working_directory": str(run.working_directory),
                "runtime_route": CONVERSATION_TURN_ROUTE,
            },
            agent_id=leader.id,
        )
        agent_event = await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.AGENT_CREATED,
            payload={
                "agent_id": str(leader.id),
                "kind": leader.kind.value,
                "profile": leader.profile,
                "model": leader.model,
            },
            agent_id=leader.id,
        )
        await self.events.publish(created_event)
        await self.events.publish(agent_event)
        return run
