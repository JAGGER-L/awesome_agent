from __future__ import annotations

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
    AgentKind,
    AgentStatus,
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunMode,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling.errors import (
    ModelErrorCode,
    ModelErrorInfo,
    ModelProviderError,
)
from awesome_agent.modeling.messages import (
    AssistantMessage,
    ModelMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from awesome_agent.modeling.stream import (
    ReasoningDelta,
    ReasoningStarted,
    TextDelta,
    ToolArgumentsDelta,
    ToolCallStarted,
    TurnCompleted,
    TurnFailed,
)
from awesome_agent.modeling.turns import ModelRequest, ModelUsage
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.registry import ToolRegistry
from awesome_agent.tools.repository import (
    execute_repository_call,
    model_tool_definitions,
)

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


class MissingThreadRepositoryContext(RuntimeError):
    pass


class ConversationService:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        runtime_repository: RuntimeRepository,
        leader_executor: LeaderTurnExecutor,
        default_model: str,
        tool_executor: ToolExecutor | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._repository = repository
        self._runtime_repository = runtime_repository
        self._leader_executor = leader_executor
        self._default_model = default_model
        self._tool_executor = tool_executor
        self._tool_registry = tool_registry

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
        turn_id = uuid4()
        trace_id = uuid4().hex
        sequence = 1
        selected_model = model or self._default_model
        turn_options: dict[str, object] = {
            "model": selected_model,
            "thinking": thinking,
            "memory": memory or {},
            "skill_ids": list(skill_ids),
        }
        run = Run(
            goal=content,
            status=RunStatus.RUNNING,
            execution_kind=ExecutionKind.CODING,
            runtime_route="leader-turn",
            dispatch_status=DispatchStatus.TERMINAL,
        )
        leader = Agent(
            run_id=run.id,
            kind=AgentKind.LEADER,
            profile="leader",
            model=selected_model,
            status=AgentStatus.RUNNING,
        )
        await self._runtime_repository.create_run(run, leader)
        run_event = await self._runtime_repository.append_event(
            run_id=run.id,
            event_type=EventType.RUN_CREATED,
            payload={
                "goal": content,
                **turn_options,
                "runtime_route": run.runtime_route or "",
                "leader_agent_id": str(leader.id),
            },
            agent_id=leader.id,
        )
        yield _event(
            ConversationStreamEventKind.TURN_STARTED,
            thread_id=thread_id,
            turn_id=turn_id,
            sequence=sequence,
            trace_id=trace_id,
            payload={
                "model": selected_model,
                "run_id": str(run.id),
                "leader_agent_id": str(leader.id),
                "runtime_event_id": str(run_event.id),
            },
        )
        await self._runtime_repository.append_event(
            run_id=run.id,
            event_type=EventType.AGENT_CREATED,
            payload={
                "agent_id": str(leader.id),
                "kind": leader.kind.value,
                "profile": leader.profile,
                "model": leader.model,
            },
            agent_id=leader.id,
        )
        user_message = await self._repository.append_message(
            thread_id=thread_id,
            role=ThreadMessageRole.USER,
            content=content,
            run_id=run.id,
            metadata={
                "run_id": str(run.id),
                "leader_agent_id": str(leader.id),
                "turn_options": turn_options,
            },
        )
        sequence += 1
        yield _event(
            ConversationStreamEventKind.MESSAGE_CREATED,
            thread_id=thread_id,
            turn_id=turn_id,
            sequence=sequence,
            trace_id=trace_id,
            payload=user_message.model_dump(mode="json"),
        )

        reasoning_active = False
        usage = ModelUsage()
        changed_files: list[dict[str, object]] = []
        try:
            messages = await self._model_messages(thread_id)
            workspace = await self._thread_workspace(thread_id)
            tools = (
                model_tool_definitions(self._tool_registry)
                if self._tool_registry is not None and workspace is not None
                else []
            )
            response_model: str | None = None
            provider: str | None = None
            response_id: str | None = None
            final_text = ""
            for _round in range(8):
                assistant_text = ""
                completed_turn: TurnCompleted | None = None
                request = ModelRequest(
                    messages=messages,
                    tools=tools,
                    thinking=thinking,
                )
                async for model_event in self._leader_executor.stream(
                    request,
                    model=selected_model,
                ):
                    if isinstance(model_event, ReasoningStarted):
                        if thinking == "off":
                            continue
                        if not reasoning_active:
                            reasoning_active = True
                            sequence += 1
                            yield _event(
                                ConversationStreamEventKind.REASONING_STARTED,
                                thread_id=thread_id,
                                turn_id=turn_id,
                                sequence=sequence,
                                trace_id=trace_id,
                                payload={},
                            )
                    elif isinstance(model_event, ReasoningDelta):
                        if thinking == "off":
                            continue
                        if not reasoning_active:
                            reasoning_active = True
                            sequence += 1
                            yield _event(
                                ConversationStreamEventKind.REASONING_STARTED,
                                thread_id=thread_id,
                                turn_id=turn_id,
                                sequence=sequence,
                                trace_id=trace_id,
                                payload={},
                            )
                        sequence += 1
                        yield _event(
                            ConversationStreamEventKind.REASONING_DELTA,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            sequence=sequence,
                            trace_id=trace_id,
                            payload={"text": model_event.text},
                        )
                    elif isinstance(model_event, TextDelta):
                        assistant_text += model_event.text
                        sequence += 1
                        yield _event(
                            ConversationStreamEventKind.MESSAGE_DELTA,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            sequence=sequence,
                            trace_id=trace_id,
                            payload={"text": model_event.text, "run_id": str(run.id)},
                        )
                    elif isinstance(model_event, ToolCallStarted):
                        sequence += 1
                        yield _event(
                            ConversationStreamEventKind.MESSAGE_DELTA,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            sequence=sequence,
                            trace_id=trace_id,
                            payload={
                                "run_id": str(run.id),
                                "tool_event": {
                                    "name": model_event.name,
                                    "summary": "requested",
                                },
                            },
                        )
                    elif isinstance(model_event, ToolArgumentsDelta):
                        continue
                    elif isinstance(model_event, TurnFailed):
                        if reasoning_active:
                            sequence += 1
                            yield _event(
                                ConversationStreamEventKind.REASONING_COMPLETED,
                                thread_id=thread_id,
                                turn_id=turn_id,
                                sequence=sequence,
                                trace_id=trace_id,
                                payload={"failed": True},
                            )
                            reasoning_active = False
                        sequence += 1
                        yield _error_event(
                            thread_id=thread_id,
                            turn_id=turn_id,
                            sequence=sequence,
                            trace_id=trace_id,
                            error=model_event.error,
                        )
                        return
                    elif isinstance(model_event, TurnCompleted):
                        completed_turn = model_event
                if completed_turn is None:
                    break
                turn = completed_turn.turn
                usage = _merge_usage(usage, turn.usage)
                response_model = turn.model
                provider = turn.provider
                response_id = turn.response_id
                if reasoning_active:
                    sequence += 1
                    yield _event(
                        ConversationStreamEventKind.REASONING_COMPLETED,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        sequence=sequence,
                        trace_id=trace_id,
                        payload={"failed": False},
                    )
                    reasoning_active = False
                assistant_message = turn.assistant
                messages.append(assistant_message)
                if not assistant_message.tool_calls:
                    final_text = assistant_message.content or assistant_text
                    break
                if self._tool_executor is None or workspace is None:
                    final_text = (
                        "I cannot access workspace tools in this conversation."
                    )
                    break
                for call in assistant_message.tool_calls:
                    result = await execute_repository_call(
                        self._tool_executor,
                        call,
                        workspace=workspace,
                        agent_id=leader.id,
                        capabilities={
                            "repository:read",
                            "repository:write",
                            "shell:execute",
                        },
                    )
                    messages.append(result)
                    effects = _changed_files_from_tool_result(result)
                    changed_files.extend(effects)
                    sequence += 1
                    yield _event(
                        ConversationStreamEventKind.MESSAGE_DELTA,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        sequence=sequence,
                        trace_id=trace_id,
                        payload={
                            "run_id": str(run.id),
                            "tool_event": {
                                "name": call.name,
                                "summary": _tool_result_summary(call.name, result),
                            },
                        },
                    )
                continue
            if _has_usage(usage):
                sequence += 1
                yield _event(
                    ConversationStreamEventKind.USAGE_UPDATED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=sequence,
                    trace_id=trace_id,
                    payload=usage.model_dump(mode="json"),
                )
            assistant_metadata: dict[str, object] = {
                "run_id": str(run.id),
                "usage": usage.model_dump(mode="json"),
            }
            if changed_files:
                assistant_metadata["changed_files"] = _dedupe_changed_files(
                    changed_files
                )
            assistant = await self._repository.append_message(
                thread_id=thread_id,
                role=ThreadMessageRole.ASSISTANT,
                content=final_text,
                run_id=run.id,
                metadata=assistant_metadata,
            )
            completed = run.model_copy(update={"status": RunStatus.COMPLETED})
            await self._runtime_repository.update_run(completed)
            await self._runtime_repository.append_event(
                run_id=run.id,
                event_type=EventType.RUN_STATUS_CHANGED,
                payload={"status": completed.status.value},
                agent_id=leader.id,
            )
            completion_payload = {
                **assistant.model_dump(mode="json"),
                "run_id": str(run.id),
                "leader_agent_id": str(leader.id),
                "requested_model": selected_model,
                "response_model": response_model,
                "provider": provider,
                "response_id": response_id,
            }
            if changed_files:
                completion_payload["changed_files"] = _dedupe_changed_files(
                    changed_files
                )
            sequence += 1
            yield _event(
                ConversationStreamEventKind.MESSAGE_COMPLETED,
                thread_id=thread_id,
                turn_id=turn_id,
                sequence=sequence,
                trace_id=trace_id,
                payload=completion_payload,
            )
            sequence += 1
            yield _event(
                ConversationStreamEventKind.TURN_COMPLETED,
                thread_id=thread_id,
                turn_id=turn_id,
                sequence=sequence,
                trace_id=trace_id,
                payload={"status": "completed"},
            )
            return
        except ModelProviderError as error:
            if reasoning_active:
                sequence += 1
                yield _event(
                    ConversationStreamEventKind.REASONING_COMPLETED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=sequence,
                    trace_id=trace_id,
                    payload={"failed": True},
                )
            sequence += 1
            await self._mark_run_failed(run, leader)
            yield _error_event(
                thread_id=thread_id,
                turn_id=turn_id,
                sequence=sequence,
                trace_id=trace_id,
                error=error.info,
            )
            return
        except Exception as error:
            if reasoning_active:
                sequence += 1
                yield _event(
                    ConversationStreamEventKind.REASONING_COMPLETED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=sequence,
                    trace_id=trace_id,
                    payload={"failed": True},
                )
            sequence += 1
            await self._mark_run_failed(run, leader)
            yield _error_event(
                thread_id=thread_id,
                turn_id=turn_id,
                sequence=sequence,
                trace_id=trace_id,
                error=ModelErrorInfo(
                    code=ModelErrorCode.PROVIDER_PROTOCOL,
                    message=str(error),
                    retryable=False,
                    provider="conversation",
                ),
            )
            return

    async def _mark_run_failed(self, run: Run, leader: Agent) -> None:
        failed = run.model_copy(update={"status": RunStatus.FAILED})
        await self._runtime_repository.update_run(failed)
        await self._runtime_repository.append_event(
            run_id=run.id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={"status": failed.status.value},
            agent_id=leader.id,
        )

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
