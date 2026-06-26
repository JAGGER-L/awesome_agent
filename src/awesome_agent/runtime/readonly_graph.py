from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
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
from awesome_agent.runtime.dispatch import (
    CorruptRuntimeStateError,
    IncompatibleGraphError,
    PermanentExecutionError,
    TransientExecutionError,
)
from awesome_agent.runtime.graphs import (
    READ_ONLY_CODING_GRAPH,
    READ_ONLY_CODING_VERSION,
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
    graph_name: str
    graph_version: int
    messages: list[dict[str, Any]]
    continuation: dict[str, Any] | None
    model_turn_count: int
    tool_call_count: int
    successful_inspections: int
    progress_fingerprints: list[str]
    stagnant_turns: int
    phase: str
    force_final: bool
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
            name=READ_ONLY_CODING_GRAPH,
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
            run.graph_name != READ_ONLY_CODING_GRAPH
            or run.graph_version != READ_ONLY_CODING_VERSION
            or run.intent is not RunIntent.READ_ONLY
        ):
            raise IncompatibleGraphError(
                f"Unsupported read-only graph: "
                f"{run.intent.value}/{run.graph_name}@{run.graph_version}"
            )
        if run.graph_thread_id is None:
            raise CorruptRuntimeStateError("Run is missing graph_thread_id.")
        if run.workspace_path is None or not run.workspace_path.is_dir():
            raise CorruptRuntimeStateError("Run workspace is unavailable.")

    async def _initialize(
        self,
        state: ReadOnlyAgentState,
    ) -> ReadOnlyAgentState:
        return {**state, "phase": "initialized"}

    async def _model_turn(
        self,
        state: ReadOnlyAgentState,
    ) -> ReadOnlyAgentState:
        if state["model_turn_count"] >= self.max_model_turns:
            raise AgentLoopFailed("Model turn budget exhausted.")
        agent = self._require_agent()
        messages = [
            _MESSAGE_ADAPTER.validate_python(item) for item in state["messages"]
        ]
        next_count = state["model_turn_count"] + 1
        force_final = next_count >= self.max_model_turns
        reminder = self._budget_reminder(next_count)
        request_messages = [
            *messages,
            *([SystemMessage(content=reminder)] if reminder else []),
        ]
        continuation = (
            ContinuationState.model_validate(state["continuation"])
            if state["continuation"] is not None
            else None
        )
        provider = self.provider_resolver(agent.model)
        try:
            turn = await provider.complete(
                ModelRequest(
                    messages=request_messages,
                    tools=model_tool_definitions(self.registry),
                    tool_choice=ToolChoice(
                        mode=(
                            ToolChoiceMode.NONE if force_final else ToolChoiceMode.AUTO
                        )
                    ),
                    continuation=continuation,
                )
            )
        except TransientModelError as error:
            raise TransientExecutionError(str(error)) from error
        except ModelProviderError as error:
            raise AgentLoopFailed(str(error)) from error
        await self._emit(
            EventType.MODEL_CALL_CREATED,
            {
                "turn": next_count,
                "status": "completed",
                "stop_reason": turn.stop_reason.value,
                "model": turn.model,
                "input_tokens": turn.usage.input_tokens,
                "output_tokens": turn.usage.output_tokens,
            },
            f"model-turn:{next_count}",
        )
        return {
            **state,
            "phase": "model_completed",
            "model_turn_count": next_count,
            "force_final": force_final,
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
        if turn.assistant.tool_calls:
            if state["force_final"]:
                return "feedback"
            return "tools"
        if (
            turn.stop_reason is StopReason.COMPLETED
            and bool(turn.assistant.content.strip())
            and state["successful_inspections"] > 0
        ):
            return "finalize"
        return "feedback"

    async def _execute_tools(
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
                result = await execute_repository_call(
                    self.executor,
                    call,
                    workspace=cast(Any, run.workspace_path),
                    agent_id=agent.id,
                )
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

    def _budget_reminder(self, next_count: int) -> str | None:
        ratio = next_count / self.max_model_turns
        if ratio >= 0.9:
            return (
                "Stop broad exploration. Produce the best evidence-based final "
                "answer soon."
            )
        if ratio >= 0.7:
            return (
                "Start converging. Inspect only evidence still needed for the answer."
            )
        return None

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


def _initial_state(run: Run, agent: Agent) -> ReadOnlyAgentState:
    return {
        "run_id": str(run.id),
        "agent_id": str(agent.id),
        "graph_name": READ_ONLY_CODING_GRAPH,
        "graph_version": READ_ONLY_CODING_VERSION,
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
    }


def _state(value: object) -> ReadOnlyAgentState:
    if not isinstance(value, dict):
        raise CorruptRuntimeStateError("Read-only graph returned invalid state.")
    required = {
        "run_id",
        "agent_id",
        "graph_name",
        "graph_version",
        "messages",
        "model_turn_count",
        "tool_call_count",
        "successful_inspections",
        "phase",
    }
    if not required.issubset(value):
        raise CorruptRuntimeStateError("Read-only graph state is incomplete.")
    return cast(ReadOnlyAgentState, value)
