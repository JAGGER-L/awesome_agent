from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from awesome_agent.modeling import (
    AssistantMessage,
    AuthenticationModelError,
    ConnectionModelError,
    ContinuationState,
    ModelErrorInfo,
    ModelMessage,
    ModelProviderError,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    ModelUsage,
    ReasoningDelta,
    StopReason,
    SystemMessage,
    TextDelta,
    TimeoutModelError,
    ToolArgumentsDelta,
    ToolCall,
    ToolCallStarted,
    ToolChoice,
    ToolChoiceMode,
    ToolDefinition,
    ToolResultMessage,
    TurnCompleted,
    TurnFailed,
    UserMessage,
    error_from_info,
)


class MessageEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    messages: tuple[ModelMessage, ...]


def _request(**updates: object) -> ModelRequest:
    payload: dict[str, object] = {
        "messages": (UserMessage(content="inspect"),),
        "thinking_enabled": False,
    }
    payload.update(updates)
    return ModelRequest.model_validate(payload)


def test_assistant_and_tool_history_round_trip_with_multiple_calls() -> None:
    envelope = MessageEnvelope(
        messages=(
            SystemMessage(content="system"),
            UserMessage(content="inspect both files"),
            AssistantMessage(
                tool_calls=(
                    ToolCall(
                        call_id="call_1",
                        name="read_file",
                        arguments_json='{"path":"a.py"}',
                    ),
                    ToolCall(
                        call_id="call_2",
                        name="mcp.docs.search",
                        arguments_json='{"query":"partial',
                    ),
                )
            ),
            ToolResultMessage(call_id="call_1", content="a", is_error=False),
            ToolResultMessage(call_id="call_2", content="failed", is_error=True),
        )
    )

    restored = MessageEnvelope.model_validate_json(envelope.model_dump_json())

    assistant = restored.messages[2]
    assert isinstance(assistant, AssistantMessage)
    assert [call.name for call in assistant.tool_calls] == [
        "read_file",
        "mcp.docs.search",
    ]
    assert assistant.tool_calls[1].arguments_json == '{"query":"partial'
    assert isinstance(restored.messages[3], ToolResultMessage)


def test_request_requires_explicit_boolean_thinking_and_supports_no_tools() -> None:
    with pytest.raises(ValidationError):
        ModelRequest.model_validate({"messages": (UserMessage(content="inspect"),)})
    with pytest.raises(ValidationError):
        _request(thinking_enabled="on")

    request = _request(
        tools=(),
        tool_choice=ToolChoice(mode=ToolChoiceMode.NONE),
    )

    assert request.thinking_enabled is False
    assert request.tools == ()
    assert request.tool_choice.mode is ToolChoiceMode.NONE


def test_specific_tool_choice_must_reference_a_defined_tool() -> None:
    with pytest.raises(ValidationError, match="defined tool"):
        _request(
            tools=(
                ToolDefinition(
                    name="read_file",
                    description="Read a file",
                    input_schema={"type": "object"},
                ),
            ),
            tool_choice=ToolChoice(mode=ToolChoiceMode.TOOL, name="execute"),
        )


def test_opaque_continuation_is_excluded_from_request_and_turn_dumps() -> None:
    continuation = ContinuationState(
        provider="deepseek",
        kind="response",
        data={"opaque": "secret-provider-token"},
    )
    request = _request(continuation=continuation)
    turn = ModelTurn(
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        assistant=AssistantMessage(content="done"),
        stop_reason=StopReason.COMPLETED,
        continuation=continuation,
    )

    assert request.continuation == continuation
    assert turn.continuation == continuation
    assert "continuation" not in request.model_dump(mode="json")
    assert "continuation" not in turn.model_dump(mode="json")
    assert "secret-provider-token" not in repr(request)
    assert "secret-provider-token" not in repr(turn)


def test_usage_addition_uses_zero_defaults() -> None:
    usage = ModelUsage(input_tokens=10, output_tokens=3, reasoning_tokens=2)

    combined = usage + ModelUsage(
        input_tokens=4,
        cache_read_tokens=8,
        cache_write_tokens=1,
    )

    assert combined == ModelUsage(
        input_tokens=14,
        output_tokens=3,
        reasoning_tokens=2,
        cache_read_tokens=8,
        cache_write_tokens=1,
    )


def test_stream_union_preserves_visible_and_terminal_events() -> None:
    adapter: TypeAdapter[ModelStreamEvent] = TypeAdapter(ModelStreamEvent)
    events: tuple[ModelStreamEvent, ...] = (
        ReasoningDelta(text="inspect"),
        TextDelta(text="result"),
        ToolCallStarted(index=0, call_id="call_1", name="read_file"),
        ToolArgumentsDelta(index=0, text='{"path":'),
        TurnCompleted(
            turn=ModelTurn(
                provider="deepseek",
                model="deepseek/deepseek-v4-flash",
                assistant=AssistantMessage(content="result"),
                stop_reason=StopReason.COMPLETED,
            )
        ),
    )

    restored = tuple(adapter.validate_json(event.model_dump_json()) for event in events)

    assert [event.type for event in restored] == [event.type for event in events]
    assert isinstance(restored[-1], TurnCompleted)


@pytest.mark.parametrize(
    ("error_type", "code", "retryable"),
    [
        (ConnectionModelError, "connection", True),
        (TimeoutModelError, "timeout", True),
        (AuthenticationModelError, "authentication", False),
    ],
)
def test_model_errors_serialize_safely_and_restore_typed_exceptions(
    error_type: type[ModelProviderError],
    code: str,
    retryable: bool,
) -> None:
    error = error_type("safe message", provider="deepseek")
    info = error.info
    restored = ModelErrorInfo.model_validate_json(info.model_dump_json())
    event = TurnFailed(error=restored)

    assert restored.code.value == code
    assert restored.retryable is retryable
    assert restored.provider == "deepseek"
    assert type(error_from_info(event.error)) is error_type


def test_model_turn_cannot_persist_reasoning_payload() -> None:
    with pytest.raises(ValidationError):
        ModelTurn.model_validate(
            {
                "provider": "deepseek",
                "model": "deepseek/deepseek-v4-flash",
                "assistant": {"role": "assistant", "content": "done"},
                "stop_reason": "completed",
                "reasoning": "must stay live",
            }
        )
