from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, cast

from openai import AsyncOpenAI

from awesome_agent.modeling import (
    AssistantMessage,
    ContinuationState,
    ModelStreamEvent,
    ModelTurn,
    ReasoningDelta,
    ReasoningSegment,
    ReasoningStarted,
    ReasoningStatus,
    ReasoningTrace,
    StopReason,
    StructuredModelProvider,
    TextDelta,
    ToolArgumentsDelta,
    ToolCall,
    ToolCallStarted,
    ToolChoiceMode,
    TurnCompleted,
    TurnFailed,
)
from awesome_agent.modeling import (
    ModelRequest as StructuredModelRequest,
)
from awesome_agent.modeling import (
    ModelUsage as StructuredModelUsage,
)
from awesome_agent.modeling.errors import ModelProviderError
from awesome_agent.modeling.messages import (
    AssistantMessage as RequestAssistantMessage,
)
from awesome_agent.modeling.messages import (
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from awesome_agent.providers.errors import classify_openai_error


class DeepSeekProvider(StructuredModelProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.deepseek.com",
        thinking_enabled: bool = True,
        reasoning_effort: Literal["high", "max"] = "high",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._thinking_enabled = thinking_enabled
        self._reasoning_effort = reasoning_effort
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    async def stream(
        self,
        request: StructuredModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=cast(Any, _deepseek_messages(request)),
                tools=cast(Any, _deepseek_tools(request)),
                tool_choice=cast(Any, _deepseek_tool_choice(request)),
                max_tokens=request.max_output_tokens,
                reasoning_effort=cast(Any, self._reasoning_effort),
                extra_body={
                    "thinking": {
                        "type": ("enabled" if self._thinking_enabled else "disabled")
                    }
                },
                stream=True,
                stream_options={"include_usage": True},
            )
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_calls: dict[int, dict[str, str]] = {}
            response_id: str | None = None
            finish_reason: str | None = None
            usage = StructuredModelUsage()
            reasoning_started = False
            async for chunk in response:
                response_id = getattr(chunk, "id", response_id)
                raw_usage = getattr(chunk, "usage", None)
                if raw_usage is not None:
                    usage = _deepseek_usage(raw_usage)
                choices = getattr(chunk, "choices", [])
                if not choices:
                    continue
                choice = choices[0]
                finish_reason = getattr(choice, "finish_reason", finish_reason)
                delta = choice.delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    if not reasoning_started:
                        reasoning_started = True
                        yield ReasoningStarted()
                    reasoning_parts.append(reasoning)
                    yield ReasoningDelta(text=reasoning)
                content = getattr(delta, "content", None)
                if content:
                    text_parts.append(content)
                    yield TextDelta(text=content)
                for raw_call in getattr(delta, "tool_calls", None) or []:
                    index = raw_call.index
                    state = tool_calls.setdefault(
                        index,
                        {"call_id": "", "name": "", "arguments": ""},
                    )
                    call_id = getattr(raw_call, "id", None)
                    function = getattr(raw_call, "function", None)
                    name = getattr(function, "name", None)
                    arguments = getattr(function, "arguments", None)
                    if call_id or name:
                        state["call_id"] = call_id or state["call_id"]
                        state["name"] = name or state["name"]
                        yield ToolCallStarted(
                            index=index,
                            call_id=state["call_id"],
                            name=state["name"],
                        )
                    if arguments:
                        state["arguments"] += arguments
                        yield ToolArgumentsDelta(index=index, text=arguments)
            calls = [
                ToolCall(
                    call_id=value["call_id"],
                    name=value["name"],
                    arguments_json=value["arguments"],
                )
                for _, value in sorted(tool_calls.items())
            ]
            reasoning_text = "".join(reasoning_parts)
            yield TurnCompleted(
                turn=ModelTurn(
                    assistant=AssistantMessage(
                        content="".join(text_parts),
                        tool_calls=calls,
                    ),
                    stop_reason=_deepseek_stop_reason(finish_reason),
                    model=self._model,
                    provider="deepseek",
                    response_id=response_id,
                    usage=usage,
                    reasoning=(
                        ReasoningTrace(
                            status=ReasoningStatus.COMPLETED,
                            segments=[
                                ReasoningSegment(
                                    sequence=1,
                                    text=reasoning_text,
                                )
                            ],
                        )
                        if reasoning_text
                        else None
                    ),
                    continuation=(
                        ContinuationState(
                            provider="deepseek",
                            kind="chat.reasoning_content",
                            data={"reasoning_content": reasoning_text},
                        )
                        if reasoning_text and calls
                        else None
                    ),
                )
            )
        except ModelProviderError as error:
            yield TurnFailed(error=error.info)
        except Exception as error:
            yield TurnFailed(
                error=classify_openai_error(error, provider="deepseek").info
            )


def _deepseek_messages(request: StructuredModelRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    continuation_reasoning = _deepseek_continuation_reasoning(request)
    assistant_indexes = [
        index
        for index, message in enumerate(request.messages)
        if isinstance(message, RequestAssistantMessage)
    ]
    last_assistant = assistant_indexes[-1] if assistant_indexes else None
    for index, message in enumerate(request.messages):
        if isinstance(message, SystemMessage | UserMessage):
            messages.append({"role": message.role, "content": message.content})
        elif isinstance(message, RequestAssistantMessage):
            mapped: dict[str, Any] = {
                "role": "assistant",
                "content": message.content or None,
            }
            if message.tool_calls:
                mapped["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments_json,
                        },
                    }
                    for call in message.tool_calls
                ]
            if index == last_assistant and continuation_reasoning is not None:
                mapped["reasoning_content"] = continuation_reasoning
            messages.append(mapped)
        elif isinstance(message, ToolResultMessage):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": message.call_id,
                    "content": message.content,
                }
            )
    return messages


def _deepseek_continuation_reasoning(
    request: StructuredModelRequest,
) -> str | None:
    continuation = request.continuation
    if (
        continuation is None
        or continuation.provider != "deepseek"
        or continuation.kind != "chat.reasoning_content"
        or not isinstance(continuation.data, dict)
    ):
        return None
    value = continuation.data.get("reasoning_content")
    return value if isinstance(value, str) else None


def _deepseek_tools(request: StructuredModelRequest) -> list[dict[str, Any]] | None:
    if not request.tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in request.tools
    ]


def _deepseek_tool_choice(request: StructuredModelRequest) -> object:
    choice = request.tool_choice
    if choice.mode is ToolChoiceMode.TOOL:
        return {"type": "function", "function": {"name": choice.name}}
    return choice.mode.value


def _deepseek_usage(usage: object) -> StructuredModelUsage:
    details = getattr(usage, "completion_tokens_details", None)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    return StructuredModelUsage(
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
        reasoning_tokens=getattr(details, "reasoning_tokens", None),
        cache_read_tokens=getattr(prompt_details, "cached_tokens", None),
    )


def _deepseek_stop_reason(value: str | None) -> StopReason:
    return {
        "stop": StopReason.COMPLETED,
        "tool_calls": StopReason.TOOL_CALLS,
        "length": StopReason.MAX_TOKENS,
        "content_filter": StopReason.CONTENT_FILTER,
    }.get(value or "", StopReason.UNKNOWN)
