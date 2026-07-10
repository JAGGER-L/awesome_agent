from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from openai import AsyncOpenAI

from awesome_agent.config import KimiRegion
from awesome_agent.modeling import (
    AssistantMessage,
    ContinuationState,
    ModelErrorCode,
    ModelRequest,
    ReasoningDelta,
    StopReason,
    TextDelta,
    ToolCall,
    ToolDefinition,
    ToolResultMessage,
    TurnCompleted,
    TurnFailed,
    UserMessage,
)


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
        id="kimi_response_1",
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
    *,
    call_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> object:
    return SimpleNamespace(
        index=0,
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


@pytest.mark.parametrize(
    ("region", "expected"),
    [
        (KimiRegion.CN, "https://api.moonshot.cn/v1"),
        (KimiRegion.GLOBAL, "https://api.moonshot.ai/v1"),
    ],
)
def test_region_constructs_only_fixed_official_endpoint(
    region: KimiRegion,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def construct(**kwargs: object) -> AsyncOpenAI:
        captured.update(kwargs)
        return _client(AsyncMock())

    import awesome_agent.providers.kimi as kimi_module

    monkeypatch.setattr(kimi_module, "AsyncOpenAI", construct)
    kimi_module.KimiProvider(
        api_key="test",
        model="kimi/kimi-k2.6",
        region=region,
    )

    assert captured["base_url"] == expected


def test_custom_region_and_non_curated_model_are_rejected() -> None:
    from awesome_agent.providers.kimi import KimiProvider

    with pytest.raises(ValueError, match="region"):
        KimiProvider(
            api_key="test",
            model="kimi/kimi-k2.6",
            region="custom",
            client=_client(AsyncMock()),
        )
    with pytest.raises(ValueError, match="curated Kimi"):
        KimiProvider(
            api_key="test",
            model="kimi/custom",
            region=KimiRegion.CN,
            client=_client(AsyncMock()),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["kimi/kimi-k2.6", "kimi/kimi-k2.5"])
async def test_both_curated_models_stream_text_reasoning_tool_and_usage(
    model: str,
) -> None:
    from awesome_agent.providers.kimi import KimiProvider

    create = AsyncMock(
        return_value=AsyncEvents(
            (
                _chunk(reasoning="Think. ", content="Working. "),
                _chunk(
                    tool_calls=(
                        _tool_delta(
                            call_id="call_1",
                            name="mcp_docs_search",
                            arguments='{"query":',
                        ),
                    )
                ),
                _chunk(
                    tool_calls=(_tool_delta(arguments='"agent"}'),),
                    finish_reason="tool_calls",
                    usage=SimpleNamespace(
                        prompt_tokens=9,
                        completion_tokens=5,
                        completion_tokens_details=SimpleNamespace(reasoning_tokens=2),
                        prompt_tokens_details=SimpleNamespace(cached_tokens=1),
                    ),
                ),
            )
        )
    )
    provider = KimiProvider(
        api_key="test",
        model=model,
        region=KimiRegion.CN,
        client=_client(create),
    )
    request = _request(
        thinking_enabled=True,
        tools=(
            ToolDefinition(name="mcp.docs.search", input_schema={"type": "object"}),
        ),
    )

    events = [event async for event in provider.stream(request)]

    assert any(isinstance(event, ReasoningDelta) for event in events)
    assert any(isinstance(event, TextDelta) for event in events)
    completed = next(event for event in events if isinstance(event, TurnCompleted)).turn
    assert completed.provider == "kimi"
    assert completed.model == model
    assert completed.stop_reason is StopReason.TOOL_CALLS
    assert completed.assistant.tool_calls[0].name == "mcp.docs.search"
    assert completed.assistant.tool_calls[0].arguments_json == '{"query":"agent"}'
    assert completed.usage.input_tokens == 9
    assert completed.usage.reasoning_tokens == 2
    assert completed.continuation is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("thinking_enabled", [False, True])
async def test_thinking_is_forwarded_explicitly(thinking_enabled: bool) -> None:
    from awesome_agent.providers.kimi import KimiProvider

    create = AsyncMock(
        return_value=AsyncEvents((_chunk(content="done", finish_reason="stop"),))
    )
    provider = KimiProvider(
        api_key="test",
        model="kimi/kimi-k2.6",
        region=KimiRegion.GLOBAL,
        client=_client(create),
    )

    async for _ in provider.stream(_request(thinking_enabled=thinking_enabled)):
        pass

    call = create.await_args
    assert call is not None
    expected = "enabled" if thinking_enabled else "disabled"
    assert call.kwargs["extra_body"]["thinking"]["type"] == expected


@pytest.mark.asyncio
async def test_assistant_history_replays_kimi_continuation() -> None:
    from awesome_agent.providers.kimi import KimiProvider

    create = AsyncMock(
        return_value=AsyncEvents((_chunk(content="done", finish_reason="stop"),))
    )
    provider = KimiProvider(
        api_key="test",
        model="kimi/kimi-k2.6",
        region=KimiRegion.CN,
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
            provider="kimi",
            kind="chat.reasoning_content",
            data={"reasoning_content": "private kimi state"},
        ),
    )

    async for _ in provider.stream(request):
        pass

    call = create.await_args
    assert call is not None
    assert call.kwargs["messages"][1]["reasoning_content"] == "private kimi state"


def _response(status: int) -> httpx.Response:
    return httpx.Response(
        status,
        request=httpx.Request("POST", "https://api.moonshot.cn/v1/chat/completions"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (
            openai.RateLimitError("private body", response=_response(429), body=None),
            ModelErrorCode.RATE_LIMIT,
            True,
        ),
        (
            openai.BadRequestError("private body", response=_response(400), body=None),
            ModelErrorCode.INVALID_REQUEST,
            False,
        ),
    ],
)
async def test_kimi_errors_use_shared_safe_classification(
    error: Exception,
    code: ModelErrorCode,
    retryable: bool,
) -> None:
    from awesome_agent.providers.kimi import KimiProvider

    provider = KimiProvider(
        api_key="test",
        model="kimi/kimi-k2.6",
        region=KimiRegion.CN,
        client=_client(AsyncMock(side_effect=error)),
    )

    events = [event async for event in provider.stream(_request())]

    failed = next(event for event in events if isinstance(event, TurnFailed))
    assert failed.error.code is code
    assert failed.error.retryable is retryable
    assert "private body" not in failed.error.message


@pytest.mark.asyncio
async def test_stream_without_finish_reason_fails_protocol() -> None:
    from awesome_agent.providers.kimi import KimiProvider

    provider = KimiProvider(
        api_key="test",
        model="kimi/kimi-k2.6",
        region=KimiRegion.CN,
        client=_client(
            AsyncMock(return_value=AsyncEvents((_chunk(content="partial"),)))
        ),
    )

    events = [event async for event in provider.stream(_request())]

    assert isinstance(events[-1], TurnFailed)
    assert events[-1].error.code is ModelErrorCode.PROVIDER_PROTOCOL
