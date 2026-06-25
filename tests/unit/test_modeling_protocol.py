from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from awesome_agent.modeling import (
    AssistantMessage,
    ContinuationState,
    ModelMessage,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    ReasoningDelta,
    ReasoningSegment,
    ReasoningStatus,
    ReasoningTrace,
    StopReason,
    StructuredModelProvider,
    SystemMessage,
    ToolCall,
    ToolChoice,
    ToolChoiceMode,
    TurnCompleted,
    UserMessage,
)


class MessageEnvelope(BaseModel):
    message: ModelMessage


def test_message_union_round_trips_multiple_tool_calls() -> None:
    envelope = MessageEnvelope(
        message=AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(call_id="call-1", name="repo.read", arguments_json='{"a":'),
                ToolCall(call_id="call-2", name="repo.list", arguments_json="{}"),
            ],
        )
    )

    restored = MessageEnvelope.model_validate_json(envelope.model_dump_json())

    assert isinstance(restored.message, AssistantMessage)
    assert restored.message.tool_calls[0].arguments_json == '{"a":'
    assert len(restored.message.tool_calls) == 2


def test_continuation_is_checkpoint_data_not_request_dump() -> None:
    request = ModelRequest(
        messages=[UserMessage(content="continue")],
        continuation=ContinuationState(
            provider="openai",
            kind="responses.output_items",
            data={"encrypted_content": "opaque"},
        ),
    )

    assert request.continuation is not None
    assert "continuation" not in request.model_dump(mode="json")


def test_reasoning_trace_joins_display_segments() -> None:
    trace = ReasoningTrace(
        status=ReasoningStatus.COMPLETED,
        segments=[
            ReasoningSegment(sequence=1, text="Inspect "),
            ReasoningSegment(sequence=2, text="tests."),
        ],
    )

    assert trace.text == "Inspect tests."


def test_specific_tool_choice_requires_name() -> None:
    with pytest.raises(ValidationError):
        ToolChoice(mode=ToolChoiceMode.TOOL)


def test_stream_event_union_round_trips() -> None:
    adapter: TypeAdapter[ModelStreamEvent] = TypeAdapter(ModelStreamEvent)
    restored = adapter.validate_json(ReasoningDelta(text="checking").model_dump_json())

    assert isinstance(restored, ReasoningDelta)


class CompleteProvider(StructuredModelProvider):
    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="done"),
                stop_reason=StopReason.COMPLETED,
                model="fake",
                provider="fake",
            )
        )


@pytest.mark.asyncio
async def test_complete_collects_terminal_turn() -> None:
    turn = await CompleteProvider().complete(
        ModelRequest(
            messages=[
                SystemMessage(content="system"),
                UserMessage(content="user"),
            ]
        )
    )

    assert turn.assistant.content == "done"
