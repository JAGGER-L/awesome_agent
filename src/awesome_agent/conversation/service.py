from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
)
from awesome_agent.conversation.models import (
    ThreadMessageKind,
    ThreadMessageRole,
)
from awesome_agent.conversation.repository import ConversationRepository
from awesome_agent.domain.enums import (
    EventType,
    RunIntent,
    RunMode,
    RunStatus,
)
from awesome_agent.domain.models import Run
from awesome_agent.modeling.errors import (
    ModelErrorCode,
    ModelErrorInfo,
)
from awesome_agent.modeling.messages import (
    AssistantMessage,
    ModelMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from awesome_agent.modeling.turns import ModelRequest, ModelUsage
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.registry import ToolRegistry

from .runtime_turns import LeaderTurnExecutor


class ThreadRunIntake(Protocol):
    async def create_run(
        self,
        *,
        repository_id: UUID,
        goal: str,
        intent: RunIntent,
        mode: RunMode = RunMode.SOLO,
    ) -> Run:
        pass


class ConversationRunIntake(Protocol):
    async def create_turn_run(
        self,
        *,
        thread_id: UUID,
        content: str,
        model: str | None,
        thinking: str | None,
        memory: dict[str, object],
        skill_ids: tuple[str, ...],
    ) -> Run:
        pass


class MissingThreadRepositoryContext(RuntimeError):
    pass


class ConversationService:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        runtime_repository: RuntimeRepository,
        conversation_run_intake: ConversationRunIntake | None = None,
        leader_executor: LeaderTurnExecutor | None = None,
        default_model: str,
        tool_executor: ToolExecutor | None = None,
        tool_registry: ToolRegistry | None = None,
        event_poll_interval: float = 0.05,
    ) -> None:
        self._repository = repository
        self._runtime_repository = runtime_repository
        self._conversation_run_intake = conversation_run_intake
        self._leader_executor = leader_executor
        self._default_model = default_model
        self._tool_executor = tool_executor
        self._tool_registry = tool_registry
        self._event_poll_interval = event_poll_interval

    async def start_turn(
        self,
        *,
        thread_id: UUID,
        content: str,
        model: str | None = None,
        thinking: str | None = None,
        memory: dict[str, object] | None = None,
        skill_ids: tuple[str, ...] = (),
    ) -> AsyncIterator[ConversationStreamEvent]:
        if self._conversation_run_intake is None:
            raise RuntimeError("Conversation runtime intake is not configured.")
        turn_id = uuid4()
        trace_id = uuid4().hex
        sequence = 1
        run = await self._conversation_run_intake.create_turn_run(
            thread_id=thread_id,
            content=content,
            model=model,
            thinking=thinking,
            memory=memory or {},
            skill_ids=skill_ids,
        )
        yield _event(
            ConversationStreamEventKind.TURN_STARTED,
            thread_id=thread_id,
            turn_id=turn_id,
            sequence=sequence,
            trace_id=trace_id,
            payload={
                "run_id": str(run.id),
                "status": run.status.value,
                "model": model or self._default_model,
            },
        )
        async for projected in self._project_run_events(
            thread_id=thread_id,
            turn_id=turn_id,
            trace_id=trace_id,
            run_id=run.id,
            after_sequence=0,
        ):
            sequence += 1
            yield projected.model_copy(update={"sequence": sequence})

    async def create_thread_run(
        self,
        *,
        thread_id: UUID,
        goal: str,
        intent: RunIntent,
        mode: RunMode,
        run_intake: ThreadRunIntake,
        repository_id: UUID | None = None,
    ) -> Run:
        thread = await self._repository.get_thread(thread_id)
        effective_repository_id = thread.repository_id or repository_id
        if effective_repository_id is None:
            raise MissingThreadRepositoryContext(
                "Thread does not have a repository_id; register a repository "
                "context before starting a Coding Run."
            )
        if thread.repository_id is None:
            thread = await self._repository.bind_repository(
                thread_id,
                effective_repository_id,
            )
        run = await run_intake.create_run(
            repository_id=thread.repository_id or effective_repository_id,
            goal=goal,
            intent=intent,
            mode=mode,
        )
        await self._repository.append_message(
            thread_id=thread_id,
            role=ThreadMessageRole.SYSTEM,
            content=f"Started Coding Run {run.id}: {goal}",
            kind=ThreadMessageKind.RUN,
            run_id=run.id,
            metadata={
                "run_id": str(run.id),
                "goal": goal,
                "status": run.status.value,
                "intent": run.intent.value,
                "mode": run.mode.value,
            },
        )
        return run

    async def list_thread_runs(self, thread_id: UUID) -> list[dict[str, object]]:
        messages = await self._repository.list_messages(thread_id)
        runs = [
            {
                **message.metadata,
                "message_id": str(message.id),
                "run_id": str(message.run_id),
                "created_at": message.created_at.isoformat(),
            }
            for message in messages
            if message.kind is ThreadMessageKind.RUN and message.run_id is not None
        ]
        return list(reversed(runs))

    async def _project_run_events(
        self,
        *,
        thread_id: UUID,
        turn_id: UUID,
        trace_id: str,
        run_id: UUID,
        after_sequence: int,
    ) -> AsyncIterator[ConversationStreamEvent]:
        last_sequence = after_sequence
        while True:
            runtime_events = await self._runtime_repository.list_events(
                run_id,
                after_sequence=last_sequence,
            )
            if not runtime_events:
                await asyncio.sleep(max(self._event_poll_interval, 0.001))
                continue
            for runtime_event in runtime_events:
                last_sequence = max(last_sequence, runtime_event.sequence)
                projected = self._project_runtime_event(
                    runtime_event,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                )
                if projected is not None:
                    yield projected
                if _is_terminal_status_event(runtime_event):
                    return

    def _project_runtime_event(
        self,
        runtime_event: object,
        *,
        thread_id: UUID,
        turn_id: UUID,
        trace_id: str,
    ) -> ConversationStreamEvent | None:
        event_type = getattr(runtime_event, "event_type", None)
        payload = getattr(runtime_event, "payload", {})
        if not isinstance(payload, dict):
            payload = {}
        if event_type is EventType.MESSAGE_CREATED:
            role = payload.get("role")
            if role == ThreadMessageRole.USER.value:
                kind = ConversationStreamEventKind.MESSAGE_CREATED
            elif role == ThreadMessageRole.ASSISTANT.value:
                kind = ConversationStreamEventKind.MESSAGE_COMPLETED
            else:
                kind = ConversationStreamEventKind.MESSAGE_DELTA
            return _event(
                kind,
                thread_id=thread_id,
                turn_id=turn_id,
                sequence=1,
                trace_id=trace_id,
                payload=payload,
            )
        if event_type is EventType.MODEL_CALL_CREATED:
            if payload.get("reasoning_started") is True:
                return _event(
                    ConversationStreamEventKind.REASONING_STARTED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    payload={},
                )
            reasoning_delta = payload.get("reasoning_delta")
            if isinstance(reasoning_delta, str) and reasoning_delta:
                return _event(
                    ConversationStreamEventKind.REASONING_DELTA,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    payload={"text": reasoning_delta},
                )
            if "reasoning_completed" in payload:
                return _event(
                    ConversationStreamEventKind.REASONING_COMPLETED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    payload={"failed": bool(payload.get("reasoning_failed", False))},
                )
            usage = {
                key: payload[key]
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                )
                if key in payload
            }
            if usage:
                return _event(
                    ConversationStreamEventKind.USAGE_UPDATED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    payload=usage,
                )
            text_delta = payload.get("text_delta")
            if isinstance(text_delta, str) and text_delta:
                return _event(
                    ConversationStreamEventKind.MESSAGE_DELTA,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    payload={
                        "text": text_delta,
                        "run_id": str(getattr(runtime_event, "run_id", "")),
                    },
                )
        if event_type is EventType.TOOL_CALL_CREATED:
            tool_name = payload.get("tool") or payload.get("name")
            return _event(
                ConversationStreamEventKind.MESSAGE_DELTA,
                thread_id=thread_id,
                turn_id=turn_id,
                sequence=1,
                trace_id=trace_id,
                payload={
                    "run_id": str(getattr(runtime_event, "run_id", "")),
                    "tool_event": {
                        "name": str(tool_name or "tool"),
                        "summary": str(payload.get("status") or "completed"),
                    },
                },
            )
        if event_type is EventType.RUN_STATUS_CHANGED:
            status = str(payload.get("status") or "")
            if status == RunStatus.COMPLETED.value:
                return _event(
                    ConversationStreamEventKind.TURN_COMPLETED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    payload={"status": status},
                )
            if status in {
                RunStatus.FAILED.value,
                RunStatus.CANCELLED.value,
                RunStatus.RECOVERY_REQUIRED.value,
            }:
                return _error_event(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    error=ModelErrorInfo(
                        code=ModelErrorCode.PROVIDER_PROTOCOL,
                        message=str(payload.get("error") or status),
                        retryable=status == RunStatus.RECOVERY_REQUIRED.value,
                        provider="runtime",
                    ),
                )
        return None

    async def _model_messages(self, thread_id: UUID) -> list[ModelMessage]:
        messages: list[ModelMessage] = []
        for message in await self._repository.list_messages(thread_id):
            if message.kind is not ThreadMessageKind.MESSAGE:
                continue
            if message.role is ThreadMessageRole.USER:
                messages.append(UserMessage(content=message.content))
            elif message.role is ThreadMessageRole.ASSISTANT:
                messages.append(AssistantMessage(content=message.content))
            elif message.role is ThreadMessageRole.SYSTEM:
                messages.append(SystemMessage(content=message.content))
        return messages

    async def _model_request(self, thread_id: UUID) -> ModelRequest:
        return ModelRequest(messages=await self._model_messages(thread_id))

    async def _thread_workspace(self, thread_id: UUID) -> Path | None:
        try:
            thread = await self._repository.get_thread(thread_id)
        except KeyError:
            return None
        if not thread.context_path:
            return None
        return Path(thread.context_path)


def _event(
    kind: ConversationStreamEventKind,
    *,
    thread_id: UUID,
    turn_id: UUID,
    sequence: int,
    trace_id: str,
    payload: dict[str, object],
) -> ConversationStreamEvent:
    return ConversationStreamEvent(
        event=kind,
        thread_id=thread_id,
        turn_id=turn_id,
        sequence=sequence,
        trace_id=trace_id,
        payload=payload,
    )


def _error_event(
    *,
    thread_id: UUID,
    turn_id: UUID,
    sequence: int,
    trace_id: str,
    error: ModelErrorInfo,
) -> ConversationStreamEvent:
    return _event(
        ConversationStreamEventKind.ERROR,
        thread_id=thread_id,
        turn_id=turn_id,
        sequence=sequence,
        trace_id=trace_id,
        payload=error.model_dump(mode="json"),
    )


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


def _tool_result_summary(tool_name: str, result: ToolResultMessage) -> str:
    if result.is_error:
        return "failed"
    if tool_name == "repo.apply_patch":
        changed = _changed_files_from_tool_result(result)
        if changed:
            names = ", ".join(str(item["path"]) for item in changed[:3])
            suffix = "" if len(changed) <= 3 else f" +{len(changed) - 3} more"
            return f"changed {names}{suffix}"
    return "completed"


def _is_terminal_status_event(runtime_event: object) -> bool:
    if getattr(runtime_event, "event_type", None) is not EventType.RUN_STATUS_CHANGED:
        return False
    payload = getattr(runtime_event, "payload", {})
    if not isinstance(payload, dict):
        return False
    return payload.get("status") in {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
        RunStatus.RECOVERY_REQUIRED.value,
    }
