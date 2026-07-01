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
    StopReason,
    ToolCall,
    ToolChoice,
    ToolChoiceMode,
    ToolDefinition,
    ToolResultMessage,
    TurnCompleted,
    UserMessage,
)
from awesome_agent.providers.deepseek import DeepSeekProvider


class AsyncEvents:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    async def __aiter__(self) -> AsyncIterator[object]:
        for event in self._events:
            yield event


def _chunk(
    *,
    reasoning: str | None = None,
    content: str | None = None,
    tool_calls: list[object] | None = None,
    finish_reason: str | None = None,
    usage: object | None = None,
) -> object:
    return SimpleNamespace(
        id="chatcmpl_123",
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                delta=SimpleNamespace(
                    reasoning_content=reasoning,
                    content=content,
                    tool_calls=tool_calls,
                ),
            )
        ],
        usage=usage,
    )


@pytest.mark.asyncio
async def test_deepseek_streams_reasoning_and_native_tool_call() -> None:
    first_call = SimpleNamespace(
        index=0,
        id="call-1",
        function=SimpleNamespace(name="repo_read", arguments='{"path":'),
    )
    second_call = SimpleNamespace(
        index=0,
        id=None,
        function=SimpleNamespace(name=None, arguments='"README.md"}'),
    )
    create = AsyncMock(
        return_value=AsyncEvents(
            [
                _chunk(reasoning="Inspect repository. "),
                _chunk(tool_calls=[first_call]),
                _chunk(
                    tool_calls=[second_call],
                    finish_reason="tool_calls",
                    usage=SimpleNamespace(
                        prompt_tokens=11,
                        completion_tokens=7,
                        completion_tokens_details=SimpleNamespace(reasoning_tokens=4),
                        prompt_tokens_details=SimpleNamespace(cached_tokens=3),
                    ),
                ),
            ]
        )
    )
    client = cast(
        AsyncOpenAI,
        cast(
            Any,
            SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
        ),
    )
    provider = DeepSeekProvider(
        api_key="test",
        model="deepseek-v4-pro",
        client=client,
    )

    events = [
        event
        async for event in provider.stream(
            ModelRequest(
                messages=[UserMessage(content="inspect")],
                tools=[
                    ToolDefinition(
                        name="repo.read",
                        input_schema={
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                        },
                    )
                ],
            )
        )
    ]

    completed = next(event for event in events if isinstance(event, TurnCompleted)).turn
    call = create.await_args
    assert call is not None
    assert call.kwargs["tools"][0]["function"]["name"] == "repo_read"
    assert completed.stop_reason is StopReason.TOOL_CALLS
    assert completed.reasoning is not None
    assert completed.reasoning.text == "Inspect repository. "
    assert completed.assistant.tool_calls[0].name == "repo.read"
    assert completed.assistant.tool_calls[0].arguments_json == ('{"path":"README.md"}')
    assert completed.usage.reasoning_tokens == 4
    assert completed.continuation is not None


@pytest.mark.asyncio
async def test_deepseek_normalizes_tool_choice_and_assistant_tool_history() -> None:
    create = AsyncMock(
        return_value=AsyncEvents([_chunk(content="done", finish_reason="stop")])
    )
    client = cast(
        AsyncOpenAI,
        cast(
            Any,
            SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
        ),
    )
    provider = DeepSeekProvider(api_key="test", model="test", client=client)

    await provider.complete(
        ModelRequest(
            messages=[
                UserMessage(content="modify"),
                AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="call-1",
                            name="repo.apply_patch",
                            arguments_json="{}",
                        )
                    ]
                ),
                ToolResultMessage(call_id="call-1", content="patched"),
            ],
            tools=[
                ToolDefinition(
                    name="repo.apply_patch",
                    input_schema={"type": "object", "properties": {}},
                )
            ],
            tool_choice=ToolChoice(
                mode=ToolChoiceMode.TOOL,
                name="repo.apply_patch",
            ),
        )
    )

    call = create.await_args
    assert call is not None
    assert call.kwargs["tools"][0]["function"]["name"] == "repo_apply_patch"
    assert call.kwargs["tool_choice"]["function"]["name"] == "repo_apply_patch"
    assert call.kwargs["messages"][1]["tool_calls"][0]["function"]["name"] == (
        "repo_apply_patch"
    )


@pytest.mark.asyncio
async def test_deepseek_replays_private_reasoning_continuation() -> None:
    create = AsyncMock(
        return_value=AsyncEvents([_chunk(content="done", finish_reason="stop")])
    )
    client = cast(
        AsyncOpenAI,
        cast(
            Any,
            SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
        ),
    )
    provider = DeepSeekProvider(api_key="test", model="test", client=client)
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
            provider="deepseek",
            kind="chat.reasoning_content",
            data={"reasoning_content": "private continuation"},
        ),
    )

    await provider.complete(request)

    call = create.await_args
    assert call is not None
    assistant = call.kwargs["messages"][1]
    assert assistant["reasoning_content"] == "private continuation"
