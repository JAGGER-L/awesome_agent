from __future__ import annotations

from typing import Literal

from awesome_agent.core.events import (
    AssistantReasoningDeltaPayload,
    AssistantTextDeltaPayload,
    EventEmitter,
    EventPayload,
    EventType,
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
    ) -> None:
        self._emitter = emitter
        self._thread_id = thread_id
        self._turn_id = turn_id
        self._operation_id = operation_id
        self._started = False
        self._terminal = False

    async def turn_started(self) -> None:
        if self._started:
            raise RuntimeError("Turn lifecycle was already started.")
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
        await self._emit(TurnLifecyclePayload(kind=event_type, reason=reason))
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
        if result.status is ToolStatus.SUCCESS:
            await self._emit(
                ToolResultPayload(
                    kind=EventType.TOOL_COMPLETED,
                    call_id=result.call_id,
                    tool_name=result.tool_name,
                    summary="Tool execution completed.",
                )
            )
            return
        assert result.error is not None
        await self._emit(
            ToolResultPayload(
                kind=EventType.TOOL_FAILED,
                call_id=result.call_id,
                tool_name=result.tool_name,
                summary=result.error.message,
                error_code=result.error.code.value,
            )
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
        )
