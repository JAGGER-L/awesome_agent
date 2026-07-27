from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from awesome_agent.contract_versions import (
    EVENT_ENVELOPE_VERSION,
    EventEnvelopeVersion,
)
from awesome_agent.core.contracts import JsonSafeInteger

_MAX_LIFECYCLE_HISTORY = 4_096

type InteractionDecisionValue = Literal[
    "trust",
    "reset_state",
    "allow_once",
    "allow_thread_writes",
    "allow_thread_network",
    "enable_full_access",
    "retry",
    "abort",
    "deny",
]


class EventType(StrEnum):
    OPERATION_STARTED = "operation.started"
    OPERATION_COMPLETED = "operation.completed"
    OPERATION_FAILED = "operation.failed"
    OPERATION_CANCELLED = "operation.cancelled"
    TURN_STARTED = "turn.started"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    TURN_CANCELLED = "turn.cancelled"
    ASSISTANT_TEXT_DELTA = "assistant.text.delta"
    ASSISTANT_REASONING_DELTA = "assistant.reasoning.delta"
    PROVIDER_RETRYING = "provider.retrying"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    TOOL_CANCELLED = "tool.cancelled"
    CONTEXT_PREPARED = "context.prepared"
    CONTEXT_COMPRESSED = "context.compressed"
    USAGE_UPDATED = "usage.updated"
    MEMORY_STATUS = "memory.status"
    INTERACTION_REQUIRED = "interaction.required"
    INTERACTION_RESOLVED = "interaction.resolved"
    WARNING = "warning"


class OperationLifecyclePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        EventType.OPERATION_STARTED,
        EventType.OPERATION_COMPLETED,
        EventType.OPERATION_FAILED,
        EventType.OPERATION_CANCELLED,
    ]
    message: str = Field(default="", max_length=2_000)


class TurnLifecyclePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        EventType.TURN_STARTED,
        EventType.TURN_COMPLETED,
        EventType.TURN_FAILED,
        EventType.TURN_CANCELLED,
    ]
    reason: str | None = Field(default=None, max_length=200)
    duration_ms: JsonSafeInteger | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_duration(self) -> TurnLifecyclePayload:
        if self.kind is EventType.TURN_STARTED and self.duration_ms is not None:
            raise ValueError("turn.started cannot include duration_ms")
        if self.kind is not EventType.TURN_STARTED and self.duration_ms is None:
            raise ValueError("Turn terminal requires duration_ms")
        return self


class AssistantTextDeltaPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[EventType.ASSISTANT_TEXT_DELTA] = EventType.ASSISTANT_TEXT_DELTA
    text: str = Field(min_length=1, max_length=30_000)


class AssistantReasoningDeltaPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[EventType.ASSISTANT_REASONING_DELTA] = (
        EventType.ASSISTANT_REASONING_DELTA
    )
    text: str = Field(min_length=1, max_length=30_000)


class ProviderRetryingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[EventType.PROVIDER_RETRYING] = EventType.PROVIDER_RETRYING
    attempt: JsonSafeInteger = Field(ge=2, le=7)
    maximum: JsonSafeInteger = Field(ge=1, le=7)
    delay_seconds: float = Field(ge=0.0, le=30.0)
    error_code: str = Field(min_length=1, max_length=128)


class ToolStartedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[EventType.TOOL_STARTED] = EventType.TOOL_STARTED
    call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=200)
    verb: str = Field(min_length=1, max_length=64)
    target: str | None = Field(default=None, max_length=2_000)


class ToolResultPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        EventType.TOOL_COMPLETED,
        EventType.TOOL_FAILED,
        EventType.TOOL_CANCELLED,
    ]
    call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=200)
    verb: str = Field(min_length=1, max_length=64)
    target: str | None = Field(default=None, max_length=2_000)
    outcome: str = Field(min_length=1, max_length=128)
    summary: str = Field(default="", max_length=2_000)
    detail: str | None = Field(default=None, max_length=4_000)
    detail_truncated_count: JsonSafeInteger | None = Field(default=None, ge=0)
    duration_ms: JsonSafeInteger = Field(ge=0)
    error_code: str | None = Field(default=None, max_length=128)


class ContextPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        EventType.CONTEXT_PREPARED,
        EventType.CONTEXT_COMPRESSED,
    ]
    source_count: JsonSafeInteger = Field(default=0, ge=0, le=10_000)
    estimated_tokens: JsonSafeInteger = Field(default=0, ge=0)


class UsageUpdatedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[EventType.USAGE_UPDATED] = EventType.USAGE_UPDATED
    input_tokens: JsonSafeInteger = Field(default=0, ge=0)
    output_tokens: JsonSafeInteger = Field(default=0, ge=0)
    reasoning_tokens: JsonSafeInteger = Field(default=0, ge=0)
    cache_read_tokens: JsonSafeInteger = Field(default=0, ge=0)
    cache_write_tokens: JsonSafeInteger = Field(default=0, ge=0)
    web_requests: JsonSafeInteger = Field(default=0, ge=0)


class MemoryStatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[EventType.MEMORY_STATUS] = EventType.MEMORY_STATUS
    layer: Literal["local", "external"]
    enabled: bool
    status: str = Field(min_length=1, max_length=128)


class InteractionChoicePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: InteractionDecisionValue
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1_000)


class InteractionRequiredPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[EventType.INTERACTION_REQUIRED] = EventType.INTERACTION_REQUIRED
    interaction_id: str = Field(min_length=1, max_length=128)
    interaction_kind: Literal[
        "workspace_trust",
        "state_reset",
        "tool_approval",
        "full_access_confirmation",
        "recovery_decision",
    ]
    prompt: str = Field(min_length=1, max_length=2_000)
    operation: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=8_000)
    capability: str | None = Field(default=None, max_length=200)
    choices: tuple[InteractionChoicePayload, ...] = Field(
        min_length=1,
        max_length=16,
    )


class InteractionResolvedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[EventType.INTERACTION_RESOLVED] = EventType.INTERACTION_RESOLVED
    interaction_id: str = Field(min_length=1, max_length=128)
    decision: InteractionDecisionValue


class WarningPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[EventType.WARNING] = EventType.WARNING
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2_000)


EventPayload = Annotated[
    OperationLifecyclePayload
    | TurnLifecyclePayload
    | AssistantTextDeltaPayload
    | AssistantReasoningDeltaPayload
    | ProviderRetryingPayload
    | ToolStartedPayload
    | ToolResultPayload
    | ContextPayload
    | UsageUpdatedPayload
    | MemoryStatusPayload
    | InteractionRequiredPayload
    | InteractionResolvedPayload
    | WarningPayload,
    Field(discriminator="kind"),
]


_OPERATION_TYPES = frozenset(
    {
        EventType.OPERATION_STARTED,
        EventType.OPERATION_COMPLETED,
        EventType.OPERATION_FAILED,
        EventType.OPERATION_CANCELLED,
    }
)
_OPERATION_TERMINALS = _OPERATION_TYPES - {EventType.OPERATION_STARTED}
_TURN_TYPES = frozenset(
    {
        EventType.TURN_STARTED,
        EventType.TURN_COMPLETED,
        EventType.TURN_FAILED,
        EventType.TURN_CANCELLED,
    }
)
_TURN_TERMINALS = _TURN_TYPES - {EventType.TURN_STARTED}


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: EventEnvelopeVersion = EVENT_ENVELOPE_VERSION
    event_id: str = Field(pattern=r"^event_[A-Za-z0-9]+$", max_length=128)
    sequence: JsonSafeInteger = Field(ge=1)
    session_id: str = Field(min_length=1, max_length=128)
    workspace_key: str = Field(min_length=1, max_length=512)
    thread_id: str | None = Field(default=None, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    operation_id: str | None = Field(default=None, max_length=128)
    client_message_id: str | None = Field(
        default=None,
        pattern=r"^client_[A-Za-z0-9_-]+$",
        max_length=128,
    )
    event_type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: EventPayload

    @model_validator(mode="after")
    def validate_envelope(self) -> EventEnvelope:
        if self.event_type.value != self.payload.kind:
            raise ValueError("event_type must match payload kind")
        if self.timestamp.utcoffset() != timedelta(0):
            raise ValueError("timestamp must use UTC")
        if self.event_type in _OPERATION_TYPES and self.operation_id is None:
            raise ValueError("operation events require operation_id")
        if self.event_type in _TURN_TYPES and (
            self.thread_id is None or self.turn_id is None
        ):
            raise ValueError("turn events require thread_id and turn_id")
        return self


class EventSink(Protocol):
    async def emit(self, event: EventEnvelope) -> None: ...


class CollectingEventSink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def emit(self, event: EventEnvelope) -> None:
        self.events.append(event)


class EventLifecycleError(RuntimeError):
    pass


class EventEmitter:
    def __init__(
        self,
        *,
        session_id: str,
        workspace_key: str,
        sink: EventSink,
        clock: Callable[[], datetime] | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._session_id = session_id
        self._workspace_key = workspace_key
        self._sink = sink
        self._clock = clock or (lambda: datetime.now(UTC))
        self._event_id_factory = event_id_factory or (lambda: f"event_{uuid4().hex}")
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._started_operations: set[str] = set()
        self._terminal_operations: OrderedDict[str, None] = OrderedDict()
        self._started_turns: set[str] = set()
        self._terminal_turns: OrderedDict[str, None] = OrderedDict()

    async def emit(
        self,
        payload: EventPayload,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        operation_id: str | None = None,
        client_message_id: str | None = None,
    ) -> EventEnvelope:
        event_type = EventType(payload.kind)
        async with self._lock:
            self._validate_transition(event_type, turn_id, operation_id)
            self._sequence += 1
            event = EventEnvelope(
                event_id=self._event_id_factory(),
                sequence=self._sequence,
                session_id=self._session_id,
                workspace_key=self._workspace_key,
                thread_id=thread_id,
                turn_id=turn_id,
                operation_id=operation_id,
                client_message_id=client_message_id,
                event_type=event_type,
                timestamp=self._clock(),
                payload=payload,
            )
            terminal = (
                event_type in _OPERATION_TERMINALS or event_type in _TURN_TERMINALS
            )
            if terminal:
                self._record_transition(event_type, turn_id, operation_id)
            await self._sink.emit(event)
            if not terminal:
                self._record_transition(event_type, turn_id, operation_id)
            return event

    def _validate_transition(
        self,
        event_type: EventType,
        turn_id: str | None,
        operation_id: str | None,
    ) -> None:
        if event_type in _OPERATION_TYPES:
            if operation_id is None:
                raise EventLifecycleError("operation events require operation_id")
            if event_type is EventType.OPERATION_STARTED:
                if (
                    operation_id in self._started_operations
                    or operation_id in self._terminal_operations
                ):
                    raise EventLifecycleError("operation already started")
            elif operation_id in self._terminal_operations:
                raise EventLifecycleError("operation already has a terminal event")
            elif operation_id not in self._started_operations:
                raise EventLifecycleError("operation terminal requires a start")
        if event_type in _TURN_TYPES:
            if turn_id is None:
                raise EventLifecycleError("turn events require turn_id")
            if event_type is EventType.TURN_STARTED:
                if turn_id in self._started_turns or turn_id in self._terminal_turns:
                    raise EventLifecycleError("turn already started")
            elif turn_id in self._terminal_turns:
                raise EventLifecycleError("turn already has a terminal event")
            elif turn_id not in self._started_turns:
                raise EventLifecycleError("turn terminal requires a start")

    def _record_transition(
        self,
        event_type: EventType,
        turn_id: str | None,
        operation_id: str | None,
    ) -> None:
        if event_type is EventType.OPERATION_STARTED:
            assert operation_id is not None
            self._started_operations.add(operation_id)
        elif event_type in _OPERATION_TERMINALS:
            assert operation_id is not None
            self._started_operations.remove(operation_id)
            self._record_terminal(self._terminal_operations, operation_id)
        if event_type is EventType.TURN_STARTED:
            assert turn_id is not None
            self._started_turns.add(turn_id)
        elif event_type in _TURN_TERMINALS:
            assert turn_id is not None
            self._started_turns.remove(turn_id)
            self._record_terminal(self._terminal_turns, turn_id)

    @staticmethod
    def _record_terminal(history: OrderedDict[str, None], identifier: str) -> None:
        history[identifier] = None
        while len(history) > _MAX_LIFECYCLE_HISTORY:
            history.popitem(last=False)
