from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from openai import AsyncOpenAI

from awesome_agent.modeling import (
    AssistantMessage,
    ContinuationState,
    ModelErrorCode,
    ModelRequest,
    ReasoningDelta,
    ReasoningStarted,
    StopReason,
    TextDelta,
    ToolArgumentsDelta,
    ToolCall,
    ToolCallStarted,
    ToolDefinition,
    ToolResultMessage,
    TurnCompleted,
    TurnFailed,
    UserMessage,
)
from awesome_agent.providers.deepseek import DeepSeekProvider


class AsyncEvents:
    def __init__(self, events: tuple[object, ...]) -> None:
        self._events = events

    async def __aiter__(self) -> AsyncIterator[object]:
        for event in self._events:
            yield event


def _chunk(
    *,
    reasoning: str | None = None,
    content: str | None = None,
    tool_calls: tuple[object, ...] = (),
    finish_reason: str | None = None,
    usage: object | None = None,
) -> object:
    return SimpleNamespace(
        id="chatcmpl_123",
        choices=(
            SimpleNamespace(
                finish_reason=finish_reason,
                delta=SimpleNamespace(
                    reasoning_content=reasoning,
                    content=content,
                    tool_calls=tool_calls,
                ),
            ),
        ),
        usage=usage,
    )


def _tool_delta(
    index: int,
    *,
    call_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> object:
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _client(create: AsyncMock) -> AsyncOpenAI:
    return cast(
        AsyncOpenAI,
        cast(
            Any,
            SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
        ),
    )


def _request(**updates: object) -> ModelRequest:
    payload: dict[str, object] = {
        "messages": (UserMessage(content="inspect"),),
        "thinking_enabled": False,
    }
    payload.update(updates)
    return ModelRequest.model_validate(payload)


async def _events(provider: DeepSeekProvider, request: ModelRequest) -> list[object]:
    return [event async for event in provider.stream(request)]


@pytest.mark.asyncio
async def test_stream_normalizes_reasoning_text_multiple_tools_usage_and_stop() -> None:
    create = AsyncMock(
        return_value=AsyncEvents(
            (
                _chunk(reasoning="Inspect. "),
                _chunk(
                    content="Working. ",
                    tool_calls=(
                        _tool_delta(
                            0,
                            call_id="call_1",
                            name="read_file",
                            arguments='{"path":',
                        ),
                        _tool_delta(
                            1,
                            call_id="call_2",
                            name="mcp_docs_search",
                            arguments='{"query":',
                        ),
                    ),
                ),
                _chunk(
                    tool_calls=(
                        _tool_delta(0, arguments='"README.md"}'),
                        _tool_delta(1, arguments='"agent"}'),
                    ),
                    finish_reason="tool_calls",
                    usage=SimpleNamespace(
                        prompt_tokens=11,
                        completion_tokens=7,
                        completion_tokens_details=SimpleNamespace(reasoning_tokens=4),
                        prompt_tokens_details=SimpleNamespace(cached_tokens=3),
                    ),
                ),
            )
        )
    )
    provider = DeepSeekProvider(
        api_key="test",
        model="deepseek/deepseek-v4-pro",
        client=_client(create),
    )
    request = _request(
        thinking_enabled=True,
        tools=(
            ToolDefinition(name="read_file", input_schema={"type": "object"}),
            ToolDefinition(name="mcp.docs.search", input_schema={"type": "object"}),
        ),
    )

    events = await _events(provider, request)

    assert isinstance(events[0], ReasoningStarted)
    assert isinstance(events[1], ReasoningDelta)
    assert any(isinstance(event, TextDelta) for event in events)
    assert sum(isinstance(event, ToolCallStarted) for event in events) == 2
    assert sum(isinstance(event, ToolArgumentsDelta) for event in events) == 4
    completed = next(event for event in events if isinstance(event, TurnCompleted)).turn
    assert completed.stop_reason is StopReason.TOOL_CALLS
    assert completed.assistant.content == "Working. "
    assert [call.name for call in completed.assistant.tool_calls] == [
        "read_file",
        "mcp.docs.search",
    ]
    assert completed.assistant.tool_calls[0].arguments_json == '{"path":"README.md"}'
    assert completed.usage.input_tokens == 11
    assert completed.usage.output_tokens == 7
    assert completed.usage.reasoning_tokens == 4
    assert completed.usage.cache_read_tokens == 3
    assert completed.continuation is not None
    assert not hasattr(completed, "reasoning")


@pytest.mark.asyncio
@pytest.mark.parametrize("thinking_enabled", [False, True])
async def test_request_thinking_is_forwarded_explicitly(
    thinking_enabled: bool,
) -> None:
    create = AsyncMock(
        return_value=AsyncEvents((_chunk(content="done", finish_reason="stop"),))
    )
    provider = DeepSeekProvider(
        api_key="test",
        model="deepseek/deepseek-v4-flash",
        client=_client(create),
    )

    await _events(provider, _request(thinking_enabled=thinking_enabled))

    call = create.await_args
    assert call is not None
    expected = "enabled" if thinking_enabled else "disabled"
    assert call.kwargs["extra_body"]["thinking"]["type"] == expected


@pytest.mark.asyncio
async def test_tool_names_are_encoded_without_collision_and_mapped_back() -> None:
    create = AsyncMock(
        return_value=AsyncEvents((_chunk(content="done", finish_reason="stop"),))
    )
    provider = DeepSeekProvider(
        api_key="test",
        model="deepseek/deepseek-v4-flash",
        client=_client(create),
    )

    await _events(
        provider,
        _request(
            tools=(
                ToolDefinition(name="mcp.a-b", input_schema={"type": "object"}),
                ToolDefinition(name="mcp.a_b", input_schema={"type": "object"}),
            )
        ),
    )

    call = create.await_args
    assert call is not None
    names = [item["function"]["name"] for item in call.kwargs["tools"]]
    assert len(names) == len(set(names)) == 2
    assert all("." not in name and "-" not in name for name in names)


@pytest.mark.asyncio
async def test_assistant_tool_history_replays_excluded_continuation() -> None:
    create = AsyncMock(
        return_value=AsyncEvents((_chunk(content="done", finish_reason="stop"),))
    )
    provider = DeepSeekProvider(
        api_key="test",
        model="deepseek/deepseek-v4-flash",
        client=_client(create),
    )
    request = _request(
        messages=(
            UserMessage(content="inspect"),
            AssistantMessage(
                tool_calls=(
                    ToolCall(call_id="call_1", name="read_file", arguments_json="{}"),
                )
            ),
            ToolResultMessage(call_id="call_1", content="result"),
        ),
        continuation=ContinuationState(
            provider="deepseek",
            kind="chat.reasoning_content",
            data={"reasoning_content": "private continuation"},
        ),
    )

    await _events(provider, request)

    call = create.await_args
    assert call is not None
    assert call.kwargs["messages"][1]["reasoning_content"] == "private continuation"


@pytest.mark.asyncio
async def test_timeout_is_explicit_and_custom_base_url_is_not_an_option() -> None:
    create = AsyncMock(
        return_value=AsyncEvents((_chunk(content="done", finish_reason="stop"),))
    )
    provider = DeepSeekProvider(
        api_key="test",
        model="deepseek/deepseek-v4-flash",
        timeout_seconds=7.5,
        client=_client(create),
    )

    await _events(provider, _request())

    call = create.await_args
    assert call is not None
    assert call.kwargs["timeout"] == 7.5
    assert "base_url" not in inspect.signature(DeepSeekProvider).parameters


@pytest.mark.parametrize(
    "model",
    ["kimi/kimi-k2.6", "deepseek/custom", "custom"],
)
def test_constructor_rejects_non_curated_deepseek_models(model: str) -> None:
    with pytest.raises(ValueError, match="curated DeepSeek"):
        DeepSeekProvider(api_key="test", model=model, client=_client(AsyncMock()))


@pytest.mark.asyncio
async def test_malformed_tool_arguments_fail_the_stream() -> None:
    create = AsyncMock(
        return_value=AsyncEvents(
            (
                _chunk(
                    tool_calls=(
                        _tool_delta(
                            0,
                            call_id="call_1",
                            name="read_file",
                            arguments="{invalid",
                        ),
                    ),
                    finish_reason="tool_calls",
                ),
            )
        )
    )
    provider = DeepSeekProvider(
        api_key="test",
        model="deepseek/deepseek-v4-flash",
        client=_client(create),
    )

    events = await _events(provider, _request())

    failed = next(event for event in events if isinstance(event, TurnFailed))
    assert failed.error.code is ModelErrorCode.PROVIDER_PROTOCOL
    assert not any(isinstance(event, TurnCompleted) for event in events)


@pytest.mark.asyncio
async def test_stream_without_finish_reason_fails_protocol() -> None:
    create = AsyncMock(return_value=AsyncEvents((_chunk(content="partial"),)))
    provider = DeepSeekProvider(
        api_key="test",
        model="deepseek/deepseek-v4-flash",
        client=_client(create),
    )

    events = await _events(provider, _request())

    assert isinstance(events[-1], TurnFailed)
    assert events[-1].error.code is ModelErrorCode.PROVIDER_PROTOCOL


def _response(status: int) -> httpx.Response:
    return httpx.Response(
        status,
        request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (
            openai.APIConnectionError(request=httpx.Request("POST", "https://test")),
            ModelErrorCode.CONNECTION,
            True,
        ),
        (
            openai.APITimeoutError(request=httpx.Request("POST", "https://test")),
            ModelErrorCode.TIMEOUT,
            True,
        ),
        (
            openai.AuthenticationError(
                "secret body", response=_response(401), body=None
            ),
            ModelErrorCode.AUTHENTICATION,
            False,
        ),
        (
            openai.RateLimitError("secret body", response=_response(429), body=None),
            ModelErrorCode.RATE_LIMIT,
            True,
        ),
        (
            openai.InternalServerError(
                "secret body", response=_response(500), body=None
            ),
            ModelErrorCode.TRANSIENT,
            True,
        ),
    ],
)
async def test_sdk_errors_are_classified_without_provider_body(
    error: Exception,
    code: ModelErrorCode,
    retryable: bool,
) -> None:
    provider = DeepSeekProvider(
        api_key="test",
        model="deepseek/deepseek-v4-flash",
        client=_client(AsyncMock(side_effect=error)),
    )

    events = await _events(provider, _request())

    failed = next(event for event in events if isinstance(event, TurnFailed))
    assert failed.error.code is code
    assert failed.error.retryable is retryable
    assert "secret body" not in failed.error.message
