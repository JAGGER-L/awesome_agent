from __future__ import annotations

import pytest

from awesome_agent.modeling.errors import ModelErrorCode, ModelErrorInfo
from awesome_agent.modeling.execution import ModelExecutionProtocolError
from awesome_agent.modeling.execution_jsonl import (
    decode_model_stream_event,
    encode_model_stream_event,
)
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.stream import (
    ModelStreamEvent,
    ReasoningDelta,
    TextDelta,
    ToolArgumentsDelta,
    ToolCallStarted,
    TurnCompleted,
    TurnFailed,
)
from awesome_agent.modeling.turns import ModelTurn, ModelUsage, StopReason

_EVENTS: list[ModelStreamEvent] = [
    TextDelta(text="hello"),
    ReasoningDelta(text="thinking"),
    ToolCallStarted(index=0, call_id="call-1", name="repo.status"),
    ToolArgumentsDelta(index=0, text='{"path":"README.md"}'),
    TurnCompleted(
        turn=ModelTurn(
            assistant=AssistantMessage(content="done"),
            stop_reason=StopReason.COMPLETED,
            model="deepseek-v4-pro",
            provider="deepseek",
            usage=ModelUsage(input_tokens=1, output_tokens=2),
        )
    ),
    TurnFailed(
        error=ModelErrorInfo(
            code=ModelErrorCode.TRANSIENT,
            message="temporary",
            retryable=True,
            provider="deepseek",
        )
    ),
]


@pytest.mark.parametrize("event", _EVENTS)
def test_model_stream_event_jsonl_round_trips(event: ModelStreamEvent) -> None:
    encoded = encode_model_stream_event(event)

    decoded = decode_model_stream_event(encoded)

    assert decoded == event


@pytest.mark.parametrize("line", ["not json", "{}", '{"type":"unknown"}'])
def test_invalid_model_stream_event_jsonl_raises_protocol_error(line: str) -> None:
    with pytest.raises(ModelExecutionProtocolError):
        decode_model_stream_event(line)
