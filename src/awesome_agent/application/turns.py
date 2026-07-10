from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from awesome_agent.agent import (
    AgentRuntimeContext,
    AgentState,
    new_agent_state,
)
from awesome_agent.application.contracts import OperationAccepted
from awesome_agent.application.events import ApplicationEventProjector
from awesome_agent.application.operations import OperationBusy, OperationController
from awesome_agent.config import TurnConfig
from awesome_agent.conversation import (
    ConversationService,
    Thread,
    Turn,
    UsageSummary,
)
from awesome_agent.core.events import EventEmitter
from awesome_agent.storage.checkpoints import TurnCheckpointStore


class AgentGraph(Protocol):
    async def ainvoke(
        self,
        state: AgentState | None,
        config: dict[str, Any],
        *,
        context: AgentRuntimeContext,
    ) -> AgentState: ...


type TurnConfigResolver = Callable[[Thread], TurnConfig]
type RuntimeContextFactory = Callable[
    [Turn, str, ApplicationEventProjector],
    AgentRuntimeContext,
]
type PostAnswerMemory = Callable[[AgentState], Awaitable[AgentState]]


class TurnExecutionFailed(RuntimeError):
    pass


async def disabled_post_answer_memory(state: AgentState) -> AgentState:
    return state


class TurnCoordinator:
    def __init__(
        self,
        *,
        workspace_key: str,
        conversation: ConversationService,
        config_resolver: TurnConfigResolver,
        graph: AgentGraph,
        runtime_context_factory: RuntimeContextFactory,
        operations: OperationController,
        emitter: EventEmitter,
        checkpoints: TurnCheckpointStore,
        seal_changes: Callable[[str], None],
        post_answer_memory: PostAnswerMemory = disabled_post_answer_memory,
    ) -> None:
        self._workspace_key = workspace_key
        self._conversation = conversation
        self._config_resolver = config_resolver
        self._graph = graph
        self._runtime_context_factory = runtime_context_factory
        self._operations = operations
        self._emitter = emitter
        self._checkpoints = checkpoints
        self._seal_changes = seal_changes
        self._post_answer_memory = post_answer_memory
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def active_operation_id(self) -> str | None:
        return self._operations.active_operation_id

    async def submit_turn(self, thread_id: str, content: str) -> OperationAccepted:
        if self._operations.active_operation_id is not None:
            raise OperationBusy("Another operation is active.")
        thread = self._conversation.read_thread(thread_id).thread
        config = self._config_resolver(thread)
        turn = self._conversation.begin_turn(thread_id, content, config)

        async def execute(operation_id: str) -> None:
            projector = ApplicationEventProjector(
                emitter=self._emitter,
                thread_id=turn.thread_id,
                turn_id=turn.id,
                operation_id=operation_id,
            )
            await projector.turn_started()
            await self._execute_turn(turn, operation_id, projector)

        try:
            handle = await self._operations.start(
                execute,
                thread_id=turn.thread_id,
                turn_id=turn.id,
            )
        except BaseException:
            self._conversation.fail_turn(turn.id, "operation_start_failed")
            raise
        self._tasks[handle.operation_id] = handle.task
        handle.task.add_done_callback(lambda _: self._trim_tasks())
        return OperationAccepted(
            operation_id=handle.operation_id,
            thread_id=thread_id,
            turn_id=turn.id,
        )

    async def wait(self, operation_id: str) -> None:
        task = self._tasks.get(operation_id)
        if task is None:
            raise KeyError(operation_id)
        try:
            await task
        finally:
            self._tasks.pop(operation_id, None)

    async def cancel_operation(self, operation_id: str) -> bool:
        return await self._operations.cancel(operation_id)

    async def _execute_turn(
        self,
        turn: Turn,
        operation_id: str,
        projector: ApplicationEventProjector,
    ) -> None:
        state = new_agent_state(
            thread_id=turn.thread_id,
            turn_id=turn.id,
            workspace_key=self._workspace_key,
            provider=turn.provider,
            model=turn.model,
            thinking_enabled=turn.thinking_enabled,
        )
        try:
            runtime = self._runtime_context_factory(turn, operation_id, projector)
        except Exception:
            self._conversation.fail_turn(turn.id, "agent_initialization_failed")
            await projector.turn_failed("agent_initialization_failed")
            await self._checkpoints.delete(turn.id)
            raise
        try:
            result = await self._graph.ainvoke(
                state,
                {
                    "configurable": {
                        "thread_id": turn.checkpoint_key,
                        "checkpoint_ns": "",
                    },
                    "recursion_limit": 2_048,
                },
                context=runtime,
            )
            result = await self._post_answer_memory(result)
        except asyncio.CancelledError:
            self._conversation.cancel_turn(turn.id)
            await projector.turn_cancelled("cancelled")
            self._seal_changes(turn.id)
            await self._checkpoints.delete(turn.id)
            raise
        except Exception:
            self._conversation.fail_turn(turn.id, "agent_execution_failed")
            await projector.turn_failed("agent_execution_failed")
            self._seal_changes(turn.id)
            await self._checkpoints.delete(turn.id)
            raise

        reason = result["termination_reason"] or "completed"
        answer = result["final_answer"]
        if answer is None:
            self._conversation.fail_turn(turn.id, reason)
            await projector.turn_failed(reason)
            self._seal_changes(turn.id)
            await self._checkpoints.delete(turn.id)
            raise TurnExecutionFailed(reason)

        self._conversation.complete_turn(
            turn.id,
            answer,
            _usage_summary(result),
            reason,
        )
        await projector.turn_completed()
        self._seal_changes(turn.id)
        await self._checkpoints.delete(turn.id)

    def _trim_tasks(self) -> None:
        while len(self._tasks) > 64:
            self._tasks.pop(next(iter(self._tasks)))


def _usage_summary(state: AgentState) -> UsageSummary:
    usage = state["usage"]
    return UsageSummary(
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        reasoning_tokens=usage.get("reasoning_tokens", 0),
        cache_read_tokens=usage.get("cache_read_tokens", 0),
        cache_write_tokens=usage.get("cache_write_tokens", 0),
        model_calls=state["model_calls"],
        tool_calls=state["tool_calls"],
        provider_retries=state["provider_retries"],
        compressions=state["compressions"],
        active_execution_seconds=state["active_execution_seconds"],
    )
