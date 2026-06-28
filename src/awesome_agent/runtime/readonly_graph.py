from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any, Literal, NotRequired, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter

from awesome_agent.domain.enums import EventType, RunIntent
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    ContinuationState,
    ModelMessage,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    ModelTurn,
    StopReason,
    SystemMessage,
    ToolChoice,
    ToolChoiceMode,
    ToolResultMessage,
    TransientModelError,
    UserMessage,
)
from awesome_agent.persistence.budget import (
    BudgetRepository,
)
from awesome_agent.runtime.agent_loop import ReadOnlyAgentLoop
from awesome_agent.runtime.agent_loop.read_only_middleware import (
    BudgetExhausted,
    ReadOnlyBudgetMiddleware,
    ReadOnlyContextMiddleware,
    ReadOnlyEvidenceMiddleware,
    ReadOnlyProgressMiddleware,
    ledger_to_state,
)
from awesome_agent.runtime.budget import (
    BudgetPolicy,
    TokenUsageDelta,
    estimate_messages_tokens,
)
from awesome_agent.runtime.context import ContextManager
from awesome_agent.runtime.dispatch import (
    CorruptRuntimeStateError,
    IncompatibleGraphError,
    PermanentExecutionError,
    TransientExecutionError,
)
from awesome_agent.runtime.graphs import (
    READ_ONLY_CODING_ROUTE,
)
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.registry import ToolRegistry
from awesome_agent.tools.repository import (
    build_read_only_executor,
    build_read_only_registry,
    execute_repository_call,
    model_tool_definitions,
)

_MESSAGE_ADAPTER: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)
_SYSTEM_PROMPT = """You are the solo Leader of a read-only coding agent.
Inspect the repository using only the provided tools and answer the user's
goal with concrete file and line evidence. Do not claim to have modified,
executed, or validated anything that the available tools cannot prove. Tool
errors are observations: correct the request and continue. Finish only when
you have enough repository evidence, and state remaining uncertainty.
"""


class ReadOnlyAgentState(TypedDict):
    run_id: str
    agent_id: str
    runtime_route: str
    messages: list[dict[str, Any]]
    continuation: dict[str, Any] | None
    model_turn_count: int
    tool_call_count: int
    successful_inspections: int
    progress_fingerprints: list[str]
    stagnant_turns: int
    phase: str
    force_final: bool
    rolling_summary: NotRequired[str]
    budget_ledger: NotRequired[dict[str, Any]]
    context_artifact_refs: NotRequired[list[str]]
    last_turn: NotRequired[dict[str, Any]]
    final_answer: NotRequired[str]
    result_summary: NotRequired[str]


EventSink = Callable[
    [EventType, dict[str, object], str],
    Awaitable[None],
]
ProviderResolver = Callable[[str], ModelProvider]
FaultHook = Callable[[str, ReadOnlyAgentState], Awaitable[None]]


class AgentLoopFailed(PermanentExecutionError):
    pass


class ReadOnlyCodingGraph:
    def __init__(
        self,
        saver: AsyncPostgresSaver,
        *,
        provider_resolver: ProviderResolver,
        registry: ToolRegistry | None = None,
        executor: ToolExecutor | None = None,
        max_model_turns: int = 60,
        max_tool_calls: int = 120,
        max_parallel_tools: int = 4,
        recursion_limit: int = 256,
        no_progress_turns: int = 8,
        fault_hook: FaultHook | None = None,
        context_manager: ContextManager | None = None,
        budget_repository: BudgetRepository | None = None,
        budget_policy: BudgetPolicy | None = None,
    ) -> None:
        self.saver = saver
        self.provider_resolver = provider_resolver
        self.registry = registry or build_read_only_registry()
        self.executor = executor or build_read_only_executor(self.registry)
        self.max_model_turns = max_model_turns
        self.max_tool_calls = max_tool_calls
        self.max_parallel_tools = max_parallel_tools
        self.recursion_limit = recursion_limit
        self.no_progress_turns = no_progress_turns
        self.fault_hook = fault_hook
        self.context_manager = context_manager
        self.budget_repository = budget_repository
        self.budget_policy = budget_policy
        self.agent_loop = ReadOnlyAgentLoop()
        self.context_middleware = ReadOnlyContextMiddleware(
            context_manager=context_manager,
            budget_repository=budget_repository,
            budget_policy=budget_policy,
            runtime_route=READ_ONLY_CODING_ROUTE,
        )
        self.budget_middleware = ReadOnlyBudgetMiddleware(
            budget_repository=budget_repository,
            budget_policy=budget_policy,
            emit=self._emit,
        )
        self.evidence_middleware = ReadOnlyEvidenceMiddleware()
        self.progress_middleware = ReadOnlyProgressMiddleware()
        self._run: Run | None = None
        self._agent: Agent | None = None
        self._event_sink: EventSink | None = None

        builder = StateGraph(ReadOnlyAgentState)
        builder.add_node("initialize", self._initialize)
        builder.add_node("model_turn", self._model_turn)
        builder.add_node("execute_tools", self._execute_tools)
        builder.add_node("feedback", self._feedback)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "initialize")
        builder.add_edge("initialize", "model_turn")
        builder.add_conditional_edges(
            "model_turn",
            self._route_turn,
            {
                "tools": "execute_tools",
                "feedback": "feedback",
                "finalize": "finalize",
            },
        )
        builder.add_edge("execute_tools", "model_turn")
        builder.add_conditional_edges(
            "feedback",
            lambda state: (
                "finalize" if state["phase"] == "forced_completion" else "model_turn"
            ),
            {
                "finalize": "finalize",
                "model_turn": "model_turn",
            },
        )
        builder.add_edge("finalize", END)
        self.graph = builder.compile(
            checkpointer=saver,
            name=READ_ONLY_CODING_ROUTE,
        )

    async def execute(
        self,
        run: Run,
        agent: Agent,
        *,
        event_sink: EventSink | None = None,
    ) -> tuple[ReadOnlyAgentState, bool]:
        self._validate_run(run)
        self._run = run
        self._agent = agent
        self._event_sink = event_sink
        config: RunnableConfig = {
            "configurable": {
                "thread_id": run.graph_thread_id,
                "checkpoint_ns": "",
            },
            "recursion_limit": self.recursion_limit,
        }
        checkpoint = await self.saver.aget_tuple(config)
        try:
            if checkpoint is None:
                result = await self.graph.ainvoke(
                    _initial_state(run, agent),
                    config,
                    durability="sync",
                )
                return _state(result), False
            snapshot = await self.graph.aget_state(config)
            if not snapshot.next:
                return _state(snapshot.values), True
            result = await self.graph.ainvoke(None, config, durability="sync")
            return _state(result), True
        finally:
            self._run = None
            self._agent = None
            self._event_sink = None

    def _validate_run(self, run: Run) -> None:
        if (
            run.runtime_route != READ_ONLY_CODING_ROUTE
            or run.intent is not RunIntent.READ_ONLY
        ):
            raise IncompatibleGraphError(
                f"Unsupported read-only graph: {run.intent.value}/{run.runtime_route}"
            )
        if run.graph_thread_id is None:
            raise CorruptRuntimeStateError("Run is missing graph_thread_id.")
        if run.workspace_path is None or not run.workspace_path.is_dir():
            raise CorruptRuntimeStateError("Run workspace is unavailable.")

    async def _initialize(
        self,
        state: ReadOnlyAgentState,
    ) -> ReadOnlyAgentState:
        return await self.agent_loop.before_agent(
            state,
            run=self._require_run(),
            agent=self._require_agent(),
            messages=self._messages_from_state(state),
            handler=self._initialize_impl,
        )

    async def _initialize_impl(
        self,
        state: ReadOnlyAgentState,
    ) -> ReadOnlyAgentState:
        return {**state, "phase": "initialized"}

    async def _model_turn(
        self,
        state: ReadOnlyAgentState,
    ) -> ReadOnlyAgentState:
        async def run_model(current: ReadOnlyAgentState) -> ReadOnlyAgentState:
            return await self.agent_loop.wrap_model_call(
                current,
                run=self._require_run(),
                agent=self._require_agent(),
                messages=self._messages_from_state(current),
                handler=self._model_turn_impl,
            )

        async def after_model(current: ReadOnlyAgentState) -> ReadOnlyAgentState:
            completed = await run_model(current)
            return await self.agent_loop.after_model(
                completed,
                run=self._require_run(),
                agent=self._require_agent(),
                messages=self._messages_from_state(completed),
                handler=_identity_state,
            )

        return await self.agent_loop.before_model(
            state,
            run=self._require_run(),
            agent=self._require_agent(),
            messages=self._messages_from_state(state),
            handler=after_model,
        )

    async def _model_turn_impl(
        self,
        state: ReadOnlyAgentState,
    ) -> ReadOnlyAgentState:
        if state["model_turn_count"] >= self.max_model_turns:
            raise AgentLoopFailed("Model turn budget exhausted.")
        run = self._require_run()
        agent = self._require_agent()
        messages = [
            _MESSAGE_ADAPTER.validate_python(item) for item in state["messages"]
        ]
        next_count = state["model_turn_count"] + 1
        force_final = state["force_final"] or next_count >= self.max_model_turns
        reminder = self.progress_middleware.budget_reminder(
            next_count=next_count,
            max_model_turns=self.max_model_turns,
        )
        ledger = await self.budget_middleware.load_ledger(
            run.id,
            state.get("budget_ledger", {}),
        )
        prepared = await self.context_middleware.prepare_context(
            run=run,
            agent=agent,
            messages=messages,
            rolling_summary=state.get("rolling_summary", ""),
        )
        checkpoint_messages = prepared.request_messages if prepared else messages
        request_messages = list(checkpoint_messages)
        if prepared is not None and prepared.compacted:
            await self.context_middleware.record_compaction(
                run=run,
                agent=agent,
                prepared=prepared,
            )
            await self._emit(
                EventType.CONTEXT_COMPACTED,
                {
                    "before_estimated_tokens": prepared.before_estimated_tokens,
                    "after_estimated_tokens": prepared.after_estimated_tokens,
                    "removed_message_count": prepared.removed_message_count,
                    "artifact_refs": prepared.artifact_refs,
                },
                f"context:{next_count}",
            )
        if prepared is not None and prepared.hard_limit_exceeded:
            force_final = True
            request_messages.append(
                SystemMessage(
                    content=(
                        "The prompt is at the hard context limit. Provide the "
                        "best bounded final answer now and do not request tools."
                    )
                )
            )
        elif reminder:
            request_messages.append(SystemMessage(content=reminder))
        try:
            ledger = await self.budget_middleware.evaluate_before_model_call(
                run_id=run.id,
                ledger=ledger,
                request_messages=request_messages,
                before_estimated_tokens=(
                    prepared.before_estimated_tokens
                    if prepared is not None
                    else estimate_messages_tokens(messages)
                ),
                turn=next_count,
            )
        except BudgetExhausted as error:
            raise AgentLoopFailed(str(error)) from error
        continuation = (
            ContinuationState.model_validate(state["continuation"])
            if state["continuation"] is not None
            else None
        )
        provider = self.provider_resolver(agent.model)
        started = monotonic()
        try:
            turn = await provider.complete(
                ModelRequest(
                    messages=request_messages,
                    tools=[] if force_final else model_tool_definitions(self.registry),
                    tool_choice=ToolChoice(
                        mode=(
                            ToolChoiceMode.NONE if force_final else ToolChoiceMode.AUTO
                        )
                    ),
                    continuation=continuation,
                )
            )
        except TransientModelError as error:
            await self._emit(
                EventType.MODEL_CALL_CREATED,
                {
                    "turn": next_count,
                    "status": "failed",
                    "provider": "unknown",
                    "model": agent.model,
                    "latency_ms": _elapsed_ms(started),
                    "error": str(error),
                },
                f"model-turn:{next_count}",
            )
            raise TransientExecutionError(str(error)) from error
        except ModelProviderError as error:
            await self._emit(
                EventType.MODEL_CALL_CREATED,
                {
                    "turn": next_count,
                    "status": "failed",
                    "provider": "unknown",
                    "model": agent.model,
                    "latency_ms": _elapsed_ms(started),
                    "error": str(error),
                },
                f"model-turn:{next_count}",
            )
            raise AgentLoopFailed(str(error)) from error
        ledger = ledger.add_usage(
            TokenUsageDelta(
                input_tokens=turn.usage.input_tokens or 0,
                output_tokens=turn.usage.output_tokens or 0,
                reasoning_tokens=turn.usage.reasoning_tokens or 0,
            )
        )
        await self.budget_middleware.persist_ledger(run.id, ledger)
        await self._emit(
            EventType.MODEL_CALL_CREATED,
            {
                "turn": next_count,
                "status": "completed",
                "stop_reason": turn.stop_reason.value,
                "provider": turn.provider,
                "model": turn.model,
                "input_tokens": turn.usage.input_tokens,
                "output_tokens": turn.usage.output_tokens,
                "reasoning_tokens": turn.usage.reasoning_tokens,
                "cache_read_tokens": turn.usage.cache_read_tokens,
                "cache_write_tokens": turn.usage.cache_write_tokens,
                "latency_ms": _elapsed_ms(started),
            },
            f"model-turn:{next_count}",
        )
        return {
            **state,
            "phase": "model_completed",
            "messages": [
                message.model_dump(mode="json") for message in checkpoint_messages
            ],
            "model_turn_count": next_count,
            "force_final": force_final,
            "rolling_summary": (
                prepared.rolling_summary
                if prepared is not None
                else state.get("rolling_summary", "")
            ),
            "budget_ledger": ledger_to_state(ledger),
            "context_artifact_refs": [
                *state.get("context_artifact_refs", []),
                *(prepared.artifact_refs if prepared is not None else []),
            ],
            "last_turn": turn.model_dump(mode="json"),
            "continuation": (
                turn.continuation.model_dump(mode="json")
                if turn.continuation is not None
                else None
            ),
        }

    def _route_turn(
        self,
        state: ReadOnlyAgentState,
    ) -> Literal["tools", "feedback", "finalize"]:
        turn = ModelTurn.model_validate(state["last_turn"])
        return cast(
            Literal["tools", "feedback", "finalize"],
            self.evidence_middleware.route_turn(
                turn=turn,
                force_final=state["force_final"],
                successful_inspections=state["successful_inspections"],
            ),
        )

    async def _execute_tools(
        self,
        state: ReadOnlyAgentState,
    ) -> ReadOnlyAgentState:
        return await self.agent_loop.wrap_tool_call(
            state,
            run=self._require_run(),
            agent=self._require_agent(),
            messages=self._messages_from_state(state),
            handler=self._execute_tools_impl,
        )

    async def _execute_tools_impl(
        self,
        state: ReadOnlyAgentState,
    ) -> ReadOnlyAgentState:
        run = self._require_run()
        agent = self._require_agent()
        turn = ModelTurn.model_validate(state["last_turn"])
        calls = turn.assistant.tool_calls
        if state["tool_call_count"] + len(calls) > self.max_tool_calls:
            return {
                **state,
                "messages": [
                    *state["messages"],
                    turn.assistant.model_dump(mode="json"),
                    SystemMessage(
                        content=(
                            "The tool-call budget is exhausted. Produce the best "
                            "evidence-based final answer without more tools."
                        )
                    ).model_dump(mode="json"),
                ],
                "force_final": True,
                "phase": "tool_budget_exhausted",
            }
        semaphore = asyncio.Semaphore(self.max_parallel_tools)

        async def execute(index: int) -> tuple[int, ToolResultMessage, str]:
            call = calls[index]
            async with semaphore:
                started = monotonic()
                result = await execute_repository_call(
                    self.executor,
                    call,
                    workspace=cast(Any, run.workspace_path),
                    agent_id=agent.id,
                )
                latency_ms = _elapsed_ms(started)
            fingerprint = hashlib.sha256(
                f"{call.name}\0{call.arguments_json}\0{result.content}".encode()
            ).hexdigest()
            await self._emit(
                EventType.TOOL_CALL_CREATED,
                {
                    "turn": state["model_turn_count"],
                    "call_id": call.call_id,
                    "tool": call.name,
                    "status": "failed" if result.is_error else "completed",
                    "result_summary": result.content[:500],
                    "latency_ms": latency_ms,
                },
                f"tool:{state['model_turn_count']}:{call.call_id}",
            )
            return index, result, fingerprint

        completed = await asyncio.gather(
            *(execute(index) for index in range(len(calls)))
        )
        ordered = sorted(completed)
        fingerprints = [fingerprint for _, _, fingerprint in ordered]
        prior = set(state["progress_fingerprints"])
        has_progress = any(fingerprint not in prior for fingerprint in fingerprints)
        stagnant = 0 if has_progress else state["stagnant_turns"] + 1
        messages = [
            *state["messages"],
            turn.assistant.model_dump(mode="json"),
            *(result.model_dump(mode="json") for _, result, _ in ordered),
        ]
        if stagnant >= self.no_progress_turns:
            messages.append(
                SystemMessage(
                    content=(
                        "You are repeating prior actions without new evidence. "
                        "Change strategy or provide a bounded answer that states "
                        "remaining uncertainty."
                    )
                ).model_dump(mode="json")
            )
        updated: ReadOnlyAgentState = {
            **state,
            "messages": messages,
            "tool_call_count": state["tool_call_count"] + len(calls),
            "successful_inspections": state["successful_inspections"]
            + sum(not result.is_error for _, result, _ in ordered),
            "progress_fingerprints": [
                *state["progress_fingerprints"],
                *fingerprints,
            ],
            "stagnant_turns": stagnant,
            "phase": "tools_completed",
        }
        if self.fault_hook is not None:
            await self.fault_hook("execute_tools", updated)
        return updated

    async def _feedback(
        self,
        state: ReadOnlyAgentState,
    ) -> ReadOnlyAgentState:
        turn = ModelTurn.model_validate(state["last_turn"])
        if state["force_final"]:
            if turn.assistant.content.strip() and state["successful_inspections"] > 0:
                return {
                    **state,
                    "last_turn": turn.model_copy(
                        update={"stop_reason": StopReason.COMPLETED}
                    ).model_dump(mode="json"),
                    "phase": "forced_completion",
                }
            raise AgentLoopFailed(
                "The final no-tool turn did not produce a supported answer."
            )
        return {
            **state,
            "messages": [
                *state["messages"],
                turn.assistant.model_dump(mode="json"),
                SystemMessage(
                    content=(
                        "Do not finish yet. Inspect the repository and support "
                        "the answer with concrete evidence."
                    )
                ).model_dump(mode="json"),
            ],
            "stagnant_turns": state["stagnant_turns"] + 1,
            "phase": "completion_rejected",
        }

    async def _finalize(
        self,
        state: ReadOnlyAgentState,
    ) -> ReadOnlyAgentState:
        finalized = await self._finalize_impl(state)
        return await self.agent_loop.after_agent(
            finalized,
            run=self._require_run(),
            agent=self._require_agent(),
            messages=self._messages_from_state(finalized),
            handler=_identity_state,
        )

    async def _finalize_impl(
        self,
        state: ReadOnlyAgentState,
    ) -> ReadOnlyAgentState:
        turn = ModelTurn.model_validate(state["last_turn"])
        answer = turn.assistant.content.strip()
        await self._emit(
            EventType.MESSAGE_CREATED,
            {
                "role": "assistant",
                "content": answer[:32768],
                "final": True,
            },
            "final-answer",
        )
        return {
            **state,
            "phase": "completed",
            "final_answer": answer[:32768],
            "result_summary": (
                f"Read-only repository task completed after "
                f"{state['model_turn_count']} model turn(s) and "
                f"{state['tool_call_count']} tool call(s)."
            ),
        }

    async def _emit(
        self,
        event_type: EventType,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        if self._event_sink is not None:
            await self._event_sink(event_type, payload, transition_id)

    def _require_run(self) -> Run:
        if self._run is None:
            raise CorruptRuntimeStateError("Graph Run context is unavailable.")
        return self._run

    def _require_agent(self) -> Agent:
        if self._agent is None:
            raise CorruptRuntimeStateError("Graph Agent context is unavailable.")
        return self._agent

    def _messages_from_state(self, state: ReadOnlyAgentState) -> list[ModelMessage]:
        return [_MESSAGE_ADAPTER.validate_python(item) for item in state["messages"]]


def _initial_state(run: Run, agent: Agent) -> ReadOnlyAgentState:
    return {
        "run_id": str(run.id),
        "agent_id": str(agent.id),
        "runtime_route": READ_ONLY_CODING_ROUTE,
        "messages": [
            SystemMessage(content=_SYSTEM_PROMPT).model_dump(mode="json"),
            UserMessage(content=run.goal).model_dump(mode="json"),
        ],
        "continuation": None,
        "model_turn_count": 0,
        "tool_call_count": 0,
        "successful_inspections": 0,
        "progress_fingerprints": [],
        "stagnant_turns": 0,
        "phase": "created",
        "force_final": False,
        "rolling_summary": "",
        "budget_ledger": {},
        "context_artifact_refs": [],
    }


def _state(value: object) -> ReadOnlyAgentState:
    if not isinstance(value, dict):
        raise CorruptRuntimeStateError("Read-only graph returned invalid state.")
    required = {
        "run_id",
        "agent_id",
        "runtime_route",
        "messages",
        "model_turn_count",
        "tool_call_count",
        "successful_inspections",
        "phase",
    }
    if not required.issubset(value):
        raise CorruptRuntimeStateError("Read-only graph state is incomplete.")
    return cast(ReadOnlyAgentState, value)


def _elapsed_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))


async def _identity_state(state: ReadOnlyAgentState) -> ReadOnlyAgentState:
    return state
