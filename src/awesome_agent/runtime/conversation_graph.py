from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from awesome_agent.conversation.models import (
    ThreadMessage,
    ThreadMessageKind,
    ThreadMessageRole,
)
from awesome_agent.conversation.repository import ConversationRepository
from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling.messages import (
    AssistantMessage,
    ModelMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from awesome_agent.modeling.provider import ModelProvider
from awesome_agent.modeling.stream import TextDelta, TurnCompleted, TurnFailed
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, ModelUsage
from awesome_agent.runtime.agent_loop import ReadOnlyAgentLoop
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.safety.redaction import redact_model_messages, redact_value
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.registry import ToolRegistry
from awesome_agent.tools.repository import (
    execute_repository_call,
    model_tool_definitions,
)

ConversationGraphState = dict[str, Any]


class ConversationGraph:
    def __init__(
        self,
        *,
        conversations: ConversationRepository,
        runtime: RuntimeRepository,
        provider_factory: Callable[[str], ModelProvider],
        default_model: str,
        agent_loop: ReadOnlyAgentLoop | None = None,
        tool_executor: ToolExecutor | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.conversations = conversations
        self.runtime = runtime
        self.provider_factory = provider_factory
        self.default_model = default_model
        self.agent_loop = agent_loop or ReadOnlyAgentLoop()
        self.tool_executor = tool_executor
        self.tool_registry = tool_registry

    async def execute(self, run: Run, leader: Agent) -> ConversationGraphState:
        created = await self._run_created_payload(run)
        thread_id = UUID(str(created["thread_id"]))
        content = str(created.get("goal") or run.goal)
        selected_model = str(created.get("model") or leader.model or self.default_model)
        thinking = _optional_str(created.get("thinking"))
        turn_options: dict[str, object] = {
            "model": selected_model,
            "thinking": thinking,
            "memory": _dict_payload(created.get("memory")),
            "skill_ids": _list_payload(created.get("skill_ids")),
        }
        return await self._execute_turn(
            run=run,
            leader=leader,
            thread_id=thread_id,
            content=content,
            selected_model=selected_model,
            thinking=thinking,
            turn_options=turn_options,
        )

    async def _execute_turn(
        self,
        *,
        run: Run,
        leader: Agent,
        thread_id: UUID,
        content: str,
        selected_model: str,
        thinking: str | None,
        turn_options: dict[str, object],
    ) -> ConversationGraphState:
        user_message = await self._message_for_run_role(
            thread_id=thread_id,
            run_id=run.id,
            role=ThreadMessageRole.USER,
        )
        if user_message is None:
            user_message = await self.conversations.append_message(
                thread_id=thread_id,
                role=ThreadMessageRole.USER,
                content=content,
                run_id=run.id,
                metadata={
                    "run_id": str(run.id),
                    "turn_options": turn_options,
                    "working_directory": str(run.working_directory)
                    if run.working_directory
                    else None,
                },
            )
            await self.runtime.append_event(
                run_id=run.id,
                event_type=EventType.MESSAGE_CREATED,
                payload={
                    "thread_id": str(thread_id),
                    "message_id": str(user_message.id),
                    "role": user_message.role.value,
                    "content": user_message.content,
                    "run_id": str(run.id),
                },
                agent_id=leader.id,
            )

        assistant = await self._message_for_run_role(
            thread_id=thread_id,
            run_id=run.id,
            role=ThreadMessageRole.ASSISTANT,
        )
        if assistant is not None:
            return _state_from_assistant_message(assistant)

        messages = await self._model_messages(thread_id)
        model_state = await self._run_model(
            run=run,
            leader=leader,
            messages=messages,
            selected_model=selected_model,
            thinking=thinking,
        )
        final_answer = str(model_state["final_answer"])
        usage = model_state["usage"]
        changed_files = model_state["changed_files"]
        assistant_metadata: dict[str, object] = {
            "run_id": str(run.id),
            "usage": usage.model_dump(mode="json"),
            "response_model": model_state.get("response_model"),
            "provider": model_state.get("provider"),
            "response_id": model_state.get("response_id"),
        }
        if changed_files:
            assistant_metadata["changed_files"] = changed_files
        assistant = await self.conversations.append_message(
            thread_id=thread_id,
            role=ThreadMessageRole.ASSISTANT,
            content=final_answer,
            run_id=run.id,
            metadata=assistant_metadata,
        )
        await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.MESSAGE_CREATED,
            payload={
                "thread_id": str(thread_id),
                "message_id": str(assistant.id),
                "role": assistant.role.value,
                "content": assistant.content,
                "run_id": str(run.id),
                "usage": usage.model_dump(mode="json"),
                "response_model": model_state.get("response_model"),
                "provider": model_state.get("provider"),
                "response_id": model_state.get("response_id"),
                "changed_files": changed_files,
            },
            agent_id=leader.id,
        )
        return {
            "final_answer": final_answer,
            "result_summary": "Conversation completed.",
            "usage": usage.model_dump(mode="json"),
            "response_model": model_state.get("response_model"),
            "provider": model_state.get("provider"),
            "response_id": model_state.get("response_id"),
            "changed_files": changed_files,
        }

    async def _run_model(
        self,
        *,
        run: Run,
        leader: Agent,
        messages: list[ModelMessage],
        selected_model: str,
        thinking: str | None,
    ) -> ConversationGraphState:
        initial_state: ConversationGraphState = {}

        async def model_call(
            current: ConversationGraphState,
        ) -> ConversationGraphState:
            return await self.agent_loop.wrap_model_call(
                current,
                run=run,
                agent=leader,
                messages=messages,
                handler=lambda _state: self._model_complete(
                    run,
                    leader,
                    selected_model,
                    messages,
                    thinking,
                ),
            )

        async def after_model(state: ConversationGraphState) -> ConversationGraphState:
            return await self.agent_loop.after_model(
                state,
                run=run,
                agent=leader,
                messages=messages,
                handler=_identity_state,
            )

        before_state: ConversationGraphState = await self.agent_loop.before_model(
            initial_state,
            run=run,
            agent=leader,
            messages=messages,
            handler=model_call,
        )
        return await after_model(before_state)

    async def _run_created_payload(self, run: Run) -> dict[str, object]:
        for event in await self.runtime.list_events(run.id):
            if event.event_type is EventType.RUN_CREATED:
                return event.payload
        raise RuntimeError("Conversation Run is missing run.created payload.")

    async def _message_for_run_role(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        role: ThreadMessageRole,
    ) -> ThreadMessage | None:
        for message in await self.conversations.list_messages(thread_id):
            if message.run_id == run_id and message.role == role:
                return message
        return None

    async def _model_messages(self, thread_id: UUID) -> list[ModelMessage]:
        messages: list[ModelMessage] = []
        for message in await self.conversations.list_messages(thread_id):
            if message.kind is not ThreadMessageKind.MESSAGE:
                continue
            if message.role is ThreadMessageRole.USER:
                messages.append(UserMessage(content=message.content))
            elif message.role is ThreadMessageRole.ASSISTANT:
                messages.append(AssistantMessage(content=message.content))
            elif message.role is ThreadMessageRole.SYSTEM:
                messages.append(SystemMessage(content=message.content))
        return messages

    async def _model_complete(
        self,
        run: Run,
        leader: Agent,
        selected_model: str,
        messages: list[ModelMessage],
        thinking: str | None,
    ) -> ConversationGraphState:
        provider = self.provider_factory(selected_model)
        model_messages = redact_model_messages(list(messages))
        tools = (
            model_tool_definitions(self.tool_registry)
            if self.tool_registry is not None and run.working_directory is not None
            else []
        )
        usage = ModelUsage()
        changed_files: list[dict[str, object]] = []
        completed: ModelTurn | None = None
        final_answer = ""
        for _round in range(8):
            final_text = ""
            completed = None
            request = ModelRequest(
                messages=model_messages,
                tools=tools,
                thinking=thinking,
            )
            async for event in provider.stream(request):
                if isinstance(event, TextDelta):
                    final_text += event.text
                    await self.runtime.append_event(
                        run_id=run.id,
                        event_type=EventType.MODEL_CALL_CREATED,
                        payload={"text_delta": event.text},
                        agent_id=leader.id,
                    )
                elif isinstance(event, TurnFailed):
                    raise RuntimeError(event.error.message)
                elif isinstance(event, TurnCompleted):
                    completed = event.turn
            if completed is None:
                raise RuntimeError("Provider stream ended without a completed turn.")
            usage = _merge_usage(usage, completed.usage)
            if _has_usage(completed.usage):
                await self.runtime.append_event(
                    run_id=run.id,
                    event_type=EventType.MODEL_CALL_CREATED,
                    payload=completed.usage.model_dump(mode="json"),
                    agent_id=leader.id,
                )
            assistant = completed.assistant
            model_messages.append(assistant)
            if not assistant.tool_calls:
                final_answer = assistant.content or final_text
                break
            if self.tool_executor is None or run.working_directory is None:
                final_answer = "I cannot access workspace tools in this conversation."
                break
            for call in assistant.tool_calls:
                result = await execute_repository_call(
                    self.tool_executor,
                    call,
                    workspace=Path(run.working_directory),
                    agent_id=leader.id,
                    capabilities={
                        "repository:read",
                        "repository:write",
                        "shell:execute",
                    },
                )
                model_messages.append(result)
                effects = _changed_files_from_tool_result(result)
                changed_files.extend(effects)
                payload: dict[str, object] = {
                    "tool": call.name,
                    "status": "failed" if result.is_error else "completed",
                    "changed_files": effects,
                }
                redacted_payload, _report = redact_value(payload)
                if isinstance(redacted_payload, dict):
                    payload = {
                        str(key): value for key, value in redacted_payload.items()
                    }
                await self.runtime.append_event(
                    run_id=run.id,
                    event_type=EventType.TOOL_CALL_CREATED,
                    payload=payload,
                    agent_id=leader.id,
                )
        if completed is None:
            raise RuntimeError("Provider stream ended without a completed turn.")
        return {
            "final_answer": final_answer,
            "usage": usage,
            "response_model": completed.model,
            "provider": completed.provider,
            "response_id": completed.response_id,
            "changed_files": _dedupe_changed_files(changed_files),
        }

async def _identity_state(state: ConversationGraphState) -> ConversationGraphState:
    return state


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _dict_payload(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list_payload(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _state_from_assistant_message(message: ThreadMessage) -> ConversationGraphState:
    usage = _dict_payload(message.metadata.get("usage"))
    changed_files = [
        item
        for item in _list_payload(message.metadata.get("changed_files"))
        if isinstance(item, dict)
    ]
    return {
        "final_answer": message.content,
        "result_summary": "Conversation completed.",
        "usage": usage,
        "response_model": message.metadata.get("response_model"),
        "provider": message.metadata.get("provider"),
        "response_id": message.metadata.get("response_id"),
        "changed_files": changed_files,
    }


def _has_usage(usage: ModelUsage) -> bool:
    return any(
        value is not None
        for value in (
            usage.input_tokens,
            usage.output_tokens,
            usage.reasoning_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
        )
    )


def _merge_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=_sum_optional(left.input_tokens, right.input_tokens),
        output_tokens=_sum_optional(left.output_tokens, right.output_tokens),
        reasoning_tokens=_sum_optional(left.reasoning_tokens, right.reasoning_tokens),
        cache_read_tokens=_sum_optional(
            left.cache_read_tokens,
            right.cache_read_tokens,
        ),
        cache_write_tokens=_sum_optional(
            left.cache_write_tokens,
            right.cache_write_tokens,
        ),
    )


def _sum_optional(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return left + right


def _changed_files_from_tool_result(
    result: ToolResultMessage,
) -> list[dict[str, object]]:
    try:
        payload = json.loads(result.content)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    paths = payload.get("paths")
    if not isinstance(paths, list):
        return []
    preimage_hashes = payload.get("preimage_hashes")
    postimage_hashes = payload.get("postimage_hashes")
    preimages = preimage_hashes if isinstance(preimage_hashes, dict) else {}
    postimages = postimage_hashes if isinstance(postimage_hashes, dict) else {}
    changed_files: list[dict[str, object]] = []
    for item in paths:
        if not isinstance(item, str):
            continue
        before = preimages.get(item)
        after = postimages.get(item)
        if after == "<missing>":
            status = "deleted"
        elif before == "<missing>":
            status = "created"
        else:
            status = "updated"
        changed_files.append({"path": item, "status": status})
    return changed_files


def _dedupe_changed_files(
    changed_files: list[dict[str, object]],
) -> list[dict[str, object]]:
    by_path: dict[str, dict[str, object]] = {}
    for item in changed_files:
        path = item.get("path")
        if isinstance(path, str):
            by_path[path] = item
    return list(by_path.values())
