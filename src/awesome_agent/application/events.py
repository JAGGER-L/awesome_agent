from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Literal

from awesome_agent.core.events import (
    AssistantReasoningDeltaPayload,
    AssistantTextDeltaPayload,
    ContextPayload,
    EventEmitter,
    EventPayload,
    EventType,
    MemoryStatusPayload,
    ProviderRetryingPayload,
    ToolResultPayload,
    TurnLifecyclePayload,
    UsageUpdatedPayload,
    WarningPayload,
)
from awesome_agent.core.tools import ToolResult, ToolStatus
from awesome_agent.modeling import (
    GatewayEvent,
    ProviderRetrying,
    ReasoningDelta,
    TextDelta,
    TurnCompleted,
    TurnFailed,
)

_MAX_DELTA_CHARS = 30_000
type TurnTerminalEventType = Literal[
    EventType.TURN_COMPLETED,
    EventType.TURN_FAILED,
    EventType.TURN_CANCELLED,
]


class ApplicationEventProjector:
    """Translate execution-layer events into the stable product event contract."""

    def __init__(
        self,
        *,
        emitter: EventEmitter,
        thread_id: str,
        turn_id: str,
        operation_id: str,
        client_message_id: str,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._emitter = emitter
        self._thread_id = thread_id
        self._turn_id = turn_id
        self._operation_id = operation_id
        self._client_message_id = client_message_id
        self._clock = clock
        self._started_at: float | None = None
        self._started = False
        self._terminal = False

    async def turn_started(self) -> None:
        if self._started:
            raise RuntimeError("Turn lifecycle was already started.")
        self._started_at = self._clock()
        await self._emit(TurnLifecyclePayload(kind=EventType.TURN_STARTED))
        self._started = True

    async def turn_completed(self) -> None:
        await self._turn_terminal(EventType.TURN_COMPLETED, None)

    async def turn_failed(self, reason: str) -> None:
        await self._turn_terminal(EventType.TURN_FAILED, reason)

    async def turn_cancelled(self, reason: str) -> None:
        await self._turn_terminal(EventType.TURN_CANCELLED, reason)

    async def _turn_terminal(
        self,
        event_type: TurnTerminalEventType,
        reason: str | None,
    ) -> None:
        if not self._started:
            raise RuntimeError("Turn terminal requires a started Turn.")
        if self._terminal:
            raise RuntimeError("Turn already has a terminal event.")
        assert self._started_at is not None
        duration_ms = max(0, round((self._clock() - self._started_at) * 1_000))
        await self._emit(
            TurnLifecyclePayload(
                kind=event_type,
                reason=reason,
                duration_ms=duration_ms,
            )
        )
        self._terminal = True

    async def project_gateway(self, event: GatewayEvent) -> None:
        if isinstance(event, TextDelta):
            await self._emit_delta(event.text, reasoning=False)
        elif isinstance(event, ReasoningDelta):
            await self._emit_delta(event.text, reasoning=True)
        elif isinstance(event, ProviderRetrying):
            await self._emit(
                ProviderRetryingPayload(
                    attempt=event.attempt,
                    maximum=event.maximum,
                    delay_seconds=event.delay_seconds,
                    error_code=event.error_code.value,
                )
            )
        elif isinstance(event, TurnCompleted):
            usage = event.turn.usage
            await self._emit(
                UsageUpdatedPayload(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    reasoning_tokens=usage.reasoning_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
                )
            )
        elif isinstance(event, TurnFailed):
            await self._emit(
                WarningPayload(
                    code=event.error.code.value,
                    message="The model request failed.",
                )
            )

    async def project_tool(self, result: ToolResult) -> None:
        presentation = result.presentation
        if presentation is None or presentation.duration_ms is None:
            raise RuntimeError("ToolResult is missing measured presentation facts.")
        if result.status is ToolStatus.SUCCESS:
            await self._emit(
                ToolResultPayload(
                    kind=EventType.TOOL_COMPLETED,
                    call_id=result.call_id,
                    tool_name=result.tool_name,
                    verb=presentation.verb,
                    target=presentation.target,
                    outcome=presentation.outcome or "Completed",
                    summary=presentation.summary,
                    detail=presentation.detail,
                    detail_truncated_count=presentation.detail_truncated_count,
                    duration_ms=presentation.duration_ms,
                )
            )
            return
        assert result.error is not None
        await self._emit(
            ToolResultPayload(
                kind=EventType.TOOL_FAILED,
                call_id=result.call_id,
                tool_name=result.tool_name,
                verb=presentation.verb,
                target=presentation.target,
                outcome=presentation.outcome or "Failed",
                summary=presentation.summary,
                detail=presentation.detail,
                detail_truncated_count=presentation.detail_truncated_count,
                duration_ms=presentation.duration_ms,
                error_code=result.error.code.value,
            )
        )

    async def project_context(
        self,
        *,
        source_count: int,
        estimated_tokens: int,
        compressed: bool,
    ) -> None:
        await self._emit(
            ContextPayload(
                kind=(
                    EventType.CONTEXT_COMPRESSED
                    if compressed
                    else EventType.CONTEXT_PREPARED
                ),
                source_count=source_count,
                estimated_tokens=estimated_tokens,
            )
        )

    async def project_warning(self, *, code: str, message: str) -> None:
        await self._emit(WarningPayload(code=code, message=message))

    async def project_memory_status(self, *, enabled: bool, status: str) -> None:
        await self._emit(
            MemoryStatusPayload(layer="external", enabled=enabled, status=status)
        )

    async def _emit_delta(self, text: str, *, reasoning: bool) -> None:
        for start in range(0, len(text), _MAX_DELTA_CHARS):
            chunk = text[start : start + _MAX_DELTA_CHARS]
            payload = (
                AssistantReasoningDeltaPayload(text=chunk)
                if reasoning
                else AssistantTextDeltaPayload(text=chunk)
            )
            await self._emit(payload)

    async def _emit(self, payload: EventPayload) -> None:
        await self._emitter.emit(
            payload,
            thread_id=self._thread_id,
            turn_id=self._turn_id,
            operation_id=self._operation_id,
            client_message_id=self._client_message_id,
        )
