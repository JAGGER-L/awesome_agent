from __future__ import annotations

from pathlib import Path
from uuid import UUID

from awesome_agent.conversation.models import ThreadMessageRole
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
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


class ProjectedConversationRunIntake:
    def __init__(
        self,
        *,
        conversations: InMemoryConversationRepository,
        runtime: InMemoryRuntimeRepository,
        assistant_content: str | None = "hello world",
        text_deltas: tuple[str, ...] = ("hello", " world"),
        usage: dict[str, object] | None = None,
        response_metadata: dict[str, object] | None = None,
        reasoning: bool = False,
        fail: bool = False,
    ) -> None:
        self.conversations = conversations
        self.runtime = runtime
        self.assistant_content = assistant_content
        self.text_deltas = text_deltas
        self.usage = usage or {}
        self.response_metadata = response_metadata or {}
        self.reasoning = reasoning
        self.fail = fail
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
        selected_model = model or "fake-model"
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
            model=selected_model,
            status=AgentStatus.READY,
        )
        await self.runtime.create_run(run, leader)
        await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.RUN_CREATED,
            payload={
                "thread_id": str(thread_id),
                "goal": content,
                "model": selected_model,
                "thinking": thinking,
                "memory": memory,
                "skill_ids": list(skill_ids),
                "attachment_ids": [str(item) for item in attachment_ids],
            },
            agent_id=leader.id,
        )
        turn_options: dict[str, object] = {
            "model": selected_model,
            "thinking": thinking,
            "memory": memory,
            "skill_ids": list(skill_ids),
            "attachment_ids": [str(item) for item in attachment_ids],
        }
        user_message = await self.conversations.append_message(
            thread_id=thread_id,
            role=ThreadMessageRole.USER,
            content=content,
            run_id=run.id,
            metadata={"run_id": str(run.id), "turn_options": turn_options},
        )
        await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.MESSAGE_CREATED,
            payload=user_message.model_dump(mode="json"),
            agent_id=leader.id,
        )
        if self.fail:
            await self.runtime.append_event(
                run_id=run.id,
                event_type=EventType.RUN_STATUS_CHANGED,
                payload={
                    "status": RunStatus.FAILED.value,
                    "error": "bad request",
                },
                agent_id=leader.id,
            )
            return run
        if self.reasoning:
            await self.runtime.append_event(
                run_id=run.id,
                event_type=EventType.MODEL_CALL_CREATED,
                payload={"reasoning_started": True},
                agent_id=leader.id,
            )
            await self.runtime.append_event(
                run_id=run.id,
                event_type=EventType.MODEL_CALL_CREATED,
                payload={"reasoning_delta": "Inspect context."},
                agent_id=leader.id,
            )
        for text in self.text_deltas:
            await self.runtime.append_event(
                run_id=run.id,
                event_type=EventType.MODEL_CALL_CREATED,
                payload={"text_delta": text},
                agent_id=leader.id,
            )
        if self.reasoning:
            await self.runtime.append_event(
                run_id=run.id,
                event_type=EventType.MODEL_CALL_CREATED,
                payload={"reasoning_completed": True},
                agent_id=leader.id,
            )
        if self.usage:
            await self.runtime.append_event(
                run_id=run.id,
                event_type=EventType.MODEL_CALL_CREATED,
                payload=self.usage,
                agent_id=leader.id,
            )
        if self.assistant_content is not None:
            assistant = await self.conversations.append_message(
                thread_id=thread_id,
                role=ThreadMessageRole.ASSISTANT,
                content=self.assistant_content,
                run_id=run.id,
                metadata={"run_id": str(run.id), **self.response_metadata},
            )
            await self.runtime.append_event(
                run_id=run.id,
                event_type=EventType.MESSAGE_CREATED,
                payload={
                    **assistant.model_dump(mode="json"),
                    **self.response_metadata,
                },
                agent_id=leader.id,
            )
        await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={"status": RunStatus.COMPLETED.value},
            agent_id=leader.id,
        )
        return run
