from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from openai import AsyncOpenAI

from awesome_agent.modeling import (
    AssistantMessage,
    ContinuationState,
    ModelRequest,
    ProviderProtocolError,
    StopReason,
    ToolCall,
    ToolResultMessage,
    TurnCompleted,
    UserMessage,
)
from awesome_agent.providers.openai import OpenAIProvider


class AsyncEvents:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    async def __aiter__(self) -> AsyncIterator[object]:
        for event in self._events:
            yield event


class ReasoningItem:
    type = "reasoning"

    def model_dump(self, *, mode: str) -> dict[str, str]:
        assert mode == "json"
        return {
            "type": "reasoning",
            "encrypted_content": "opaque",
        }


@pytest.mark.asyncio
async def test_openai_maps_reasoning_summary_and_function_call() -> None:
    completed_response = SimpleNamespace(
        id="resp_123",
        status="completed",
        incomplete_details=None,
        output=[ReasoningItem()],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            output_tokens_details=SimpleNamespace(reasoning_tokens=3),
            input_tokens_details=SimpleNamespace(cached_tokens=2),
        ),
    )
    create = AsyncMock(
        return_value=AsyncEvents(
            [
                SimpleNamespace(
                    type="response.reasoning_summary_text.delta",
                    delta="Inspect tests.",
                ),
                SimpleNamespace(
                    type="response.output_item.added",
                    output_index=1,
                    item=SimpleNamespace(
                        type="function_call",
                        call_id="call-1",
                        name="repo.read",
                    ),
                ),
                SimpleNamespace(
                    type="response.function_call_arguments.delta",
                    output_index=1,
                    delta='{"path":"README.md"}',
                ),
                SimpleNamespace(
                    type="response.completed",
                    response=completed_response,
                ),
            ]
        )
    )
    client = cast(
        AsyncOpenAI,
        cast(Any, SimpleNamespace(responses=SimpleNamespace(create=create))),
    )
    provider = OpenAIProvider(api_key="test", model="test-model", client=client)

    events = [
        event
        async for event in provider.stream(
            ModelRequest(messages=[UserMessage(content="inspect")])
        )
    ]

    turn = next(event for event in events if isinstance(event, TurnCompleted)).turn
    assert turn.stop_reason is StopReason.TOOL_CALLS
    assert turn.reasoning is not None
    assert turn.reasoning.text == "Inspect tests."
    assert turn.assistant.tool_calls[0].call_id == "call-1"
    assert turn.usage.cache_read_tokens == 2
    assert turn.continuation is not None
    assert "continuation" not in turn.model_dump(mode="json")


@pytest.mark.asyncio
async def test_openai_inserts_reasoning_items_before_assistant_tool_call() -> None:
    completed_response = SimpleNamespace(
        id="resp_123",
        status="completed",
        incomplete_details=None,
        output=[],
        usage=None,
    )
    create = AsyncMock(
        return_value=AsyncEvents(
            [
                SimpleNamespace(
                    type="response.output_text.delta",
                    delta="done",
                ),
                SimpleNamespace(
                    type="response.completed",
                    response=completed_response,
                ),
            ]
        )
    )
    client = cast(
        AsyncOpenAI,
        cast(Any, SimpleNamespace(responses=SimpleNamespace(create=create))),
    )
    provider = OpenAIProvider(api_key="test", model="test-model", client=client)
    request = ModelRequest(
        messages=[
            UserMessage(content="inspect"),
            AssistantMessage(
                tool_calls=[
                    ToolCall(
                        call_id="call-1",
                        name="repo.read",
                        arguments_json="{}",
                    )
                ]
            ),
            ToolResultMessage(call_id="call-1", content="result"),
        ],
        continuation=ContinuationState(
            provider="openai",
            kind="responses.reasoning_items",
            data={
                "items": [
                    {
                        "type": "reasoning",
                        "encrypted_content": "opaque",
                    }
                ]
            },
        ),
    )

    turn = await provider.complete(request)

    assert turn.assistant.content == "done"
    call = create.await_args
    assert call is not None
    input_items = call.kwargs["input"]
    assert input_items[1]["type"] == "reasoning"
    assert input_items[2]["type"] == "function_call"


@pytest.mark.asyncio
async def test_openai_complete_raises_classified_protocol_error() -> None:
    create = AsyncMock(side_effect=RuntimeError("malformed provider response"))
    client = cast(
        AsyncOpenAI,
        cast(Any, SimpleNamespace(responses=SimpleNamespace(create=create))),
    )
    provider = OpenAIProvider(api_key="test", model="test-model", client=client)

    with pytest.raises(ProviderProtocolError) as captured:
        await provider.complete(ModelRequest(messages=[UserMessage(content="inspect")]))

    assert captured.value.info.provider == "openai"
