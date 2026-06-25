from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast

from openai import AsyncOpenAI
from pydantic import JsonValue

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
from awesome_agent.providers.base import ModelRequest, ModelResult, ModelUsage
from awesome_agent.providers.errors import classify_openai_error


class OpenAIProvider(StructuredModelProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._client = client or AsyncOpenAI(api_key=api_key)

    async def generate(self, request: ModelRequest) -> ModelResult:
        turn = await self.complete(
            StructuredModelRequest(
                messages=[
                    SystemMessage(content=request.system_prompt),
                    UserMessage(content=request.user_prompt),
                ],
                max_output_tokens=request.max_output_tokens,
            )
        )
        return ModelResult(
            text=turn.assistant.content,
            model=turn.model,
            provider=turn.provider,
            response_id=turn.response_id,
            usage=ModelUsage(
                input_tokens=turn.usage.input_tokens or 0,
                output_tokens=turn.usage.output_tokens or 0,
            ),
        )

    async def stream(
        self,
        request: StructuredModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        try:
            response = await self._client.responses.create(
                model=cast(Any, self._model),
                input=cast(Any, _openai_input(request)),
                tools=cast(Any, _openai_tools(request)),
                tool_choice=cast(Any, _openai_tool_choice(request)),
                max_output_tokens=request.max_output_tokens,
                include=["reasoning.encrypted_content"],
                reasoning={"summary": "auto"},
                store=False,
                stream=True,
            )
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_calls: dict[int, dict[str, str]] = {}
            reasoning_started = False
            async for event in response:
                raw_event = cast(Any, event)
                event_type = getattr(raw_event, "type", "")
                if event_type == "response.reasoning_summary_text.delta":
                    delta = getattr(raw_event, "delta", "")
                    if delta:
                        if not reasoning_started:
                            reasoning_started = True
                            yield ReasoningStarted()
                        reasoning_parts.append(delta)
                        yield ReasoningDelta(text=delta)
                elif event_type == "response.output_text.delta":
                    delta = getattr(raw_event, "delta", "")
                    if delta:
                        text_parts.append(delta)
                        yield TextDelta(text=delta)
                elif event_type == "response.output_item.added":
                    item = getattr(raw_event, "item", None)
                    if getattr(item, "type", None) == "function_call":
                        index = getattr(raw_event, "output_index", 0)
                        state = tool_calls.setdefault(
                            index,
                            {
                                "call_id": getattr(item, "call_id", ""),
                                "name": getattr(item, "name", ""),
                                "arguments": "",
                            },
                        )
                        yield ToolCallStarted(
                            index=index,
                            call_id=state["call_id"],
                            name=state["name"],
                        )
                elif event_type == "response.function_call_arguments.delta":
                    index = getattr(raw_event, "output_index", 0)
                    delta = getattr(raw_event, "delta", "")
                    state = tool_calls.setdefault(
                        index,
                        {"call_id": "", "name": "", "arguments": ""},
                    )
                    state["arguments"] += delta
                    yield ToolArgumentsDelta(index=index, text=delta)
                elif event_type == "response.completed":
                    completed = raw_event.response
                    output_items = [
                        _json_item(item)
                        for item in getattr(completed, "output", [])
                        if getattr(item, "type", None) == "reasoning"
                    ]
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
                            stop_reason=_openai_stop_reason(completed, calls),
                            model=self._model,
                            provider="openai",
                            response_id=getattr(completed, "id", None),
                            usage=_openai_usage(getattr(completed, "usage", None)),
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
                                    provider="openai",
                                    kind="responses.reasoning_items",
                                    data=cast(
                                        JsonValue,
                                        {"items": output_items},
                                    ),
                                )
                                if output_items
                                else None
                            ),
                        )
                    )
                elif event_type == "response.failed":
                    error = getattr(raw_event.response, "error", None)
                    raise ValueError(
                        getattr(error, "message", None) or "OpenAI response failed."
                    )
        except ModelProviderError as error:
            yield TurnFailed(error=error.info)
        except Exception as error:
            yield TurnFailed(error=classify_openai_error(error, provider="openai").info)


def _openai_input(request: StructuredModelRequest) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    continuation_items = _openai_continuation_items(request)
    inserted_continuation = False
    for message in request.messages:
        if isinstance(message, SystemMessage | UserMessage):
            items.append({"role": message.role, "content": message.content})
        elif isinstance(message, RequestAssistantMessage):
            if continuation_items and not inserted_continuation:
                items.extend(continuation_items)
                inserted_continuation = True
            if message.content:
                items.append({"role": "assistant", "content": message.content})
            items.extend(
                {
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments_json,
                }
                for call in message.tool_calls
            )
        elif isinstance(message, ToolResultMessage):
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.call_id,
                    "output": message.content,
                }
            )
    if continuation_items and not inserted_continuation:
        items = [*continuation_items, *items]
    return items


def _openai_continuation_items(
    request: StructuredModelRequest,
) -> list[dict[str, Any]]:
    continuation = request.continuation
    if (
        continuation is None
        or continuation.provider != "openai"
        or continuation.kind != "responses.reasoning_items"
        or not isinstance(continuation.data, dict)
    ):
        return []
    items = continuation.data.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _openai_tools(request: StructuredModelRequest) -> list[dict[str, Any]] | None:
    if not request.tools:
        return None
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
            "strict": True,
        }
        for tool in request.tools
    ]


def _openai_tool_choice(request: StructuredModelRequest) -> object:
    choice = request.tool_choice
    if choice.mode is ToolChoiceMode.TOOL:
        return {"type": "function", "name": choice.name}
    return choice.mode.value


def _openai_usage(usage: object | None) -> StructuredModelUsage:
    if usage is None:
        return StructuredModelUsage()
    output_details = getattr(usage, "output_tokens_details", None)
    input_details = getattr(usage, "input_tokens_details", None)
    return StructuredModelUsage(
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        reasoning_tokens=getattr(output_details, "reasoning_tokens", None),
        cache_read_tokens=getattr(input_details, "cached_tokens", None),
    )


def _openai_stop_reason(response: object, calls: list[ToolCall]) -> StopReason:
    if calls:
        return StopReason.TOOL_CALLS
    status = getattr(response, "status", None)
    if status == "completed":
        return StopReason.COMPLETED
    details = getattr(response, "incomplete_details", None)
    reason = getattr(details, "reason", None)
    if reason == "max_output_tokens":
        return StopReason.MAX_TOKENS
    if reason == "content_filter":
        return StopReason.CONTENT_FILTER
    return StopReason.UNKNOWN


def _json_item(item: object) -> dict[str, Any]:
    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json")
        if isinstance(value, dict):
            return cast(dict[str, Any], value)
    if isinstance(item, dict):
        return cast(dict[str, Any], item)
    raise ValueError("OpenAI reasoning item is not JSON serializable.")
