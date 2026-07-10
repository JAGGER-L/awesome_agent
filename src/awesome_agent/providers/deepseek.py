from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from openai import AsyncOpenAI

from awesome_agent.modeling import (
    AssistantMessage,
    ContinuationState,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    ModelUsage,
    ProviderProtocolError,
    ReasoningDelta,
    ReasoningStarted,
    StopReason,
    SystemMessage,
    TextDelta,
    ToolArgumentsDelta,
    ToolCall,
    ToolCallStarted,
    ToolChoiceMode,
    ToolResultMessage,
    TurnCompleted,
    TurnFailed,
    UserMessage,
)
from awesome_agent.modeling.errors import ModelProviderError
from awesome_agent.providers.errors import classify_openai_error

DEEPSEEK_OFFICIAL_BASE_URL = "https://api.deepseek.com"
_CURATED_MODELS = {
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-pro",
}
_WIRE_UNSAFE = re.compile(r"[^A-Za-z0-9_]")


class DeepSeekProvider:
    provider_id: ClassVar[str] = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        client: AsyncOpenAI | None = None,
    ) -> None:
        if model not in _CURATED_MODELS:
            raise ValueError("Model must be a curated DeepSeek model.")
        if not api_key.strip():
            raise ValueError("DeepSeek API key cannot be empty.")
        if timeout_seconds <= 0:
            raise ValueError("DeepSeek timeout must be positive.")
        self._model = model
        self._wire_model = model.split("/", maxsplit=1)[1]
        self._timeout_seconds = timeout_seconds
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_OFFICIAL_BASE_URL,
            timeout=timeout_seconds,
        )

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        tool_names = _tool_names(request)
        try:
            response = await self._client.chat.completions.create(
                model=self._wire_model,
                messages=cast(Any, _messages(request, tool_names)),
                tools=cast(Any, _tools(request, tool_names)),
                tool_choice=cast(Any, _tool_choice(request, tool_names)),
                max_tokens=request.max_output_tokens,
                extra_body={
                    "thinking": {
                        "type": ("enabled" if request.thinking_enabled else "disabled")
                    }
                },
                timeout=self._timeout_seconds,
                stream=True,
                stream_options={"include_usage": True},
            )
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_calls: dict[int, _ToolAssembly] = {}
            response_id: str | None = None
            finish_reason: str | None = None
            usage = ModelUsage()
            reasoning_started = False
            async for chunk in response:
                response_id = getattr(chunk, "id", response_id)
                raw_usage = getattr(chunk, "usage", None)
                if raw_usage is not None:
                    usage = _usage(raw_usage)
                choices = getattr(chunk, "choices", ())
                if not choices:
                    continue
                choice = choices[0]
                current_finish = getattr(choice, "finish_reason", None)
                if current_finish is not None:
                    finish_reason = current_finish
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
                for raw_call in getattr(delta, "tool_calls", None) or ():
                    index = raw_call.index
                    state = tool_calls.setdefault(index, _ToolAssembly())
                    call_id = getattr(raw_call, "id", None)
                    function = getattr(raw_call, "function", None)
                    wire_name = getattr(function, "name", None)
                    arguments = getattr(function, "arguments", None)
                    if call_id:
                        state.call_id = call_id
                    if wire_name:
                        state.name = tool_names.wire_to_model.get(
                            wire_name,
                            wire_name,
                        )
                    if not state.started and state.call_id and state.name:
                        state.started = True
                        yield ToolCallStarted(
                            index=index,
                            call_id=state.call_id,
                            name=state.name,
                        )
                    if arguments:
                        state.arguments += arguments
                        yield ToolArgumentsDelta(index=index, text=arguments)
            if finish_reason is None:
                raise ProviderProtocolError(
                    "Provider stream ended without a finish reason.",
                    provider="deepseek",
                )
            calls = _completed_calls(tool_calls)
            reasoning_text = "".join(reasoning_parts)
            yield TurnCompleted(
                turn=ModelTurn(
                    provider="deepseek",
                    model=self._model,
                    assistant=AssistantMessage(
                        content="".join(text_parts),
                        tool_calls=calls,
                    ),
                    stop_reason=_stop_reason(finish_reason),
                    response_id=response_id,
                    usage=usage,
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


@dataclass(slots=True)
class _ToolAssembly:
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    started: bool = False


@dataclass(frozen=True, slots=True)
class _ToolNames:
    model_to_wire: dict[str, str]
    wire_to_model: dict[str, str]


def _tool_names(request: ModelRequest) -> _ToolNames:
    model_to_wire: dict[str, str] = {}
    wire_to_model: dict[str, str] = {}
    for tool in request.tools:
        wire = _unique_wire_name(tool.name, wire_to_model)
        model_to_wire[tool.name] = wire
        wire_to_model[wire] = tool.name
    return _ToolNames(model_to_wire=model_to_wire, wire_to_model=wire_to_model)


def _unique_wire_name(model_name: str, wire_to_model: dict[str, str]) -> str:
    root = _wire_name(model_name)
    if root not in wire_to_model or wire_to_model[root] == model_name:
        return root
    suffix = hashlib.sha256(model_name.encode()).hexdigest()[:8]
    candidate = f"{root}_{suffix}"
    if candidate in wire_to_model and wire_to_model[candidate] != model_name:
        raise ValueError("Tool names collide after Provider encoding.")
    return candidate


def _wire_name(model_name: str) -> str:
    return _WIRE_UNSAFE.sub("_", model_name) or "tool"


def _messages(request: ModelRequest, tool_names: _ToolNames) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    continuation_reasoning = _continuation_reasoning(request)
    assistant_indexes = [
        index
        for index, message in enumerate(request.messages)
        if isinstance(message, AssistantMessage)
    ]
    last_assistant = assistant_indexes[-1] if assistant_indexes else None
    for index, message in enumerate(request.messages):
        if isinstance(message, SystemMessage | UserMessage):
            result.append({"role": message.role, "content": message.content})
        elif isinstance(message, AssistantMessage):
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
                            "name": tool_names.model_to_wire.get(
                                call.name,
                                _wire_name(call.name),
                            ),
                            "arguments": call.arguments_json,
                        },
                    }
                    for call in message.tool_calls
                ]
            if index == last_assistant and continuation_reasoning is not None:
                mapped["reasoning_content"] = continuation_reasoning
            result.append(mapped)
        elif isinstance(message, ToolResultMessage):
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": message.call_id,
                    "content": message.content,
                }
            )
    return result


def _continuation_reasoning(request: ModelRequest) -> str | None:
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


def _tools(
    request: ModelRequest, tool_names: _ToolNames
) -> list[dict[str, Any]] | None:
    if not request.tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": tool_names.model_to_wire[tool.name],
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in request.tools
    ]


def _tool_choice(request: ModelRequest, tool_names: _ToolNames) -> object:
    choice = request.tool_choice
    if choice.mode is ToolChoiceMode.TOOL:
        name = cast(str, choice.name)
        return {
            "type": "function",
            "function": {"name": tool_names.model_to_wire[name]},
        }
    return choice.mode.value


def _usage(usage: object) -> ModelUsage:
    completion_details = getattr(usage, "completion_tokens_details", None)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    return ModelUsage(
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        reasoning_tokens=getattr(completion_details, "reasoning_tokens", 0) or 0,
        cache_read_tokens=getattr(prompt_details, "cached_tokens", 0) or 0,
    )


def _completed_calls(states: dict[int, _ToolAssembly]) -> tuple[ToolCall, ...]:
    result: list[ToolCall] = []
    for _, state in sorted(states.items()):
        if not state.call_id or not state.name or not state.started:
            raise ProviderProtocolError(
                "Provider returned an incomplete tool call.",
                provider="deepseek",
            )
        try:
            arguments = json.loads(state.arguments)
        except json.JSONDecodeError as error:
            raise ProviderProtocolError(
                "Provider returned malformed tool arguments.",
                provider="deepseek",
            ) from error
        if not isinstance(arguments, dict):
            raise ProviderProtocolError(
                "Provider tool arguments must be a JSON object.",
                provider="deepseek",
            )
        result.append(
            ToolCall(
                call_id=state.call_id,
                name=state.name,
                arguments_json=state.arguments,
            )
        )
    return tuple(result)


def _stop_reason(value: str) -> StopReason:
    return {
        "stop": StopReason.COMPLETED,
        "tool_calls": StopReason.TOOL_CALLS,
        "length": StopReason.MAX_TOKENS,
        "content_filter": StopReason.CONTENT_FILTER,
    }.get(value, StopReason.UNKNOWN)
