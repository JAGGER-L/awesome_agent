from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from uuid import UUID

from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
)
from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import RuntimeEvent
from awesome_agent.modeling.provider import ModelProvider
from awesome_agent.modeling.stream import ModelStreamEvent
from awesome_agent.modeling.turns import ModelRequest


class LeaderTurnExecutor:
    def stream(
        self,
        request: ModelRequest,
        *,
        model: str,
    ) -> AsyncIterator[ModelStreamEvent]:
        raise NotImplementedError


@dataclass(slots=True)
class ProviderLeaderTurnExecutor(LeaderTurnExecutor):
    provider_factory: Callable[[str], ModelProvider]

    async def stream(
        self,
        request: ModelRequest,
        *,
        model: str,
    ) -> AsyncIterator[ModelStreamEvent]:
        provider = self.provider_factory(model)
        async for event in provider.stream(request):
            yield event


def project_runtime_event(
    *,
    thread_id: UUID,
    turn_id: UUID,
    event: RuntimeEvent,
) -> list[ConversationStreamEvent]:
    if event.event_type is EventType.RUN_CREATED:
        return [
            _conversation_event(
                ConversationStreamEventKind.TURN_STARTED,
                thread_id=thread_id,
                turn_id=turn_id,
                event=event,
                payload={
                    "run_id": str(event.run_id),
                    **event.payload,
                },
            )
        ]
    if event.event_type is EventType.MODEL_CALL_CREATED:
        text = event.payload.get("text_delta")
        if isinstance(text, str):
            return [
                _conversation_event(
                    ConversationStreamEventKind.MESSAGE_DELTA,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event=event,
                    payload={"text": text, "run_id": str(event.run_id)},
                )
            ]
    if event.event_type is EventType.TOOL_CALL_CREATED:
        return [
            _conversation_event(
                ConversationStreamEventKind.MESSAGE_DELTA,
                thread_id=thread_id,
                turn_id=turn_id,
                event=event,
                payload={
                    "run_id": str(event.run_id),
                    "tool_event": {
                        key: value
                        for key, value in event.payload.items()
                        if key not in {"prompt", "message", "secret", "api_key"}
                    },
                },
            )
        ]
    if event.event_type in {
        EventType.TEAM_CHILD_RUN_CREATED,
        EventType.TEAM_SUBAGENT_REQUESTED,
        EventType.TEAM_ASSIGNMENT_CREATED,
        EventType.TEAM_MAILBOX_MESSAGE_CREATED,
    }:
        return [
            _conversation_event(
                ConversationStreamEventKind.MESSAGE_DELTA,
                thread_id=thread_id,
                turn_id=turn_id,
                event=event,
                payload={
                    "run_id": str(event.run_id),
                    "team_event": {
                        key: value
                        for key, value in event.payload.items()
                        if key not in {"prompt", "message", "secret", "api_key"}
                    },
                },
            )
        ]
    return []


def _conversation_event(
    kind: ConversationStreamEventKind,
    *,
    thread_id: UUID,
    turn_id: UUID,
    event: RuntimeEvent,
    payload: dict[str, object],
) -> ConversationStreamEvent:
    return ConversationStreamEvent(
        event=kind,
        thread_id=thread_id,
        turn_id=turn_id,
        sequence=event.sequence,
        created_at=event.created_at,
        trace_id=event.trace_id or event.run_id.hex,
        payload=payload,
    )
