from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventType(StrEnum):
    INTERACTION_REQUIRED = "interaction_required"
    TOOL_STARTED = "tool_started"
    TOOL_RESULT = "tool_result"
    WORKSPACE_CHANGED = "workspace_changed"
    OPERATION_COMPLETED = "operation_completed"
    OPERATION_FAILED = "operation_failed"
    OPERATION_CANCELLED = "operation_cancelled"


class InteractionRequiredPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal[EventType.INTERACTION_REQUIRED] = EventType.INTERACTION_REQUIRED
    interaction_id: str
    interaction_kind: Literal["workspace_trust", "execute_boundary"]
    prompt: str
    choices: tuple[str, ...]


class ToolStartedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal[EventType.TOOL_STARTED] = EventType.TOOL_STARTED
    call_id: str
    tool_name: str


class ToolResultPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal[EventType.TOOL_RESULT] = EventType.TOOL_RESULT
    call_id: str
    tool_name: str
    status: Literal["success", "error"]
    content: str = Field(max_length=30_000)
    error_code: str | None = None


class WorkspaceChangedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal[EventType.WORKSPACE_CHANGED] = EventType.WORKSPACE_CHANGED
    change_set_id: str
    paths: tuple[str, ...]
    reversibility: Literal["full", "partial", "none"]


class OperationTerminalPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal[
        EventType.OPERATION_COMPLETED,
        EventType.OPERATION_FAILED,
        EventType.OPERATION_CANCELLED,
    ]
    operation_id: str
    message: str = Field(default="", max_length=2_000)


EventPayload = Annotated[
    InteractionRequiredPayload
    | ToolStartedPayload
    | ToolResultPayload
    | WorkspaceChangedPayload
    | OperationTerminalPayload,
    Field(discriminator="kind"),
]


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: Literal[1] = 1
    session_id: str
    turn_id: str | None
    sequence: int = Field(ge=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: EventType
    payload: EventPayload

    @model_validator(mode="after")
    def event_type_matches_payload(self) -> EventEnvelope:
        if self.event_type.value != self.payload.kind:
            raise ValueError("event_type must match payload kind")
        return self


class EventSink(Protocol):
    async def emit(self, event: EventEnvelope) -> None: ...


class CollectingEventSink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def emit(self, event: EventEnvelope) -> None:
        self.events.append(event)


class EventEmitter:
    def __init__(
        self,
        *,
        session_id: str,
        sink: EventSink,
    ) -> None:
        self._session_id = session_id
        self._sink = sink
        self._sequence = 0

    async def emit(
        self,
        payload: EventPayload,
        *,
        turn_id: str | None = None,
    ) -> EventEnvelope:
        self._sequence += 1
        event = EventEnvelope(
            session_id=self._session_id,
            turn_id=turn_id,
            sequence=self._sequence,
            event_type=EventType(payload.kind),
            payload=payload,
        )
        await self._sink.emit(event)
        return event
