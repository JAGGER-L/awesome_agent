from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from awesome_agent.attachments.service import AttachmentService
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
        extension_catalog_version: str | None = None,
        attachment_service: AttachmentService | None = None,
    ) -> None:
        self.conversations = conversations
        self.runtime = runtime
        self.events = events
        self.default_model = default_model
        self.extension_catalog_version = extension_catalog_version
        self.attachment_service = attachment_service

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
            extension_catalog_version=self.extension_catalog_version,
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
        user_message_id = uuid4()
        attachments = []
        if self.attachment_service is not None and attachment_ids:
            attachments = await self.attachment_service.bind_to_run(
                thread_id=thread_id,
                attachment_ids=list(attachment_ids),
                run_id=run.id,
                message_id=user_message_id,
            )
        payload: dict[str, object] = {
            "thread_id": str(thread_id),
            "goal": content,
            "user_message_id": str(user_message_id),
            "model": selected_model,
            "thinking": thinking,
            "memory": memory,
            "skill_ids": list(skill_ids),
            "attachment_ids": [str(item.id) for item in attachments],
            "attachments": [item.snapshot() for item in attachments],
            "working_directory": str(run.working_directory),
            "runtime_route": CONVERSATION_TURN_ROUTE,
        }
        if self.extension_catalog_version is not None:
            payload["extension_catalog_version"] = self.extension_catalog_version
        created_event = await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.RUN_CREATED,
            payload=payload,
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
        attachment_events = []
        for attachment in attachments:
            attachment_events.append(
                await self.runtime.append_event(
                    run_id=run.id,
                    event_type=EventType.ATTACHMENT_ATTACHED,
                    payload={
                        "attachment_id": str(attachment.id),
                        "filename": attachment.filename,
                        "media_type": attachment.media_type.value,
                        "size": attachment.size,
                    },
                    agent_id=leader.id,
                )
            )
        await self.events.publish(created_event)
        await self.events.publish(agent_event)
        for event in attachment_events:
            await self.events.publish(event)
        return run
