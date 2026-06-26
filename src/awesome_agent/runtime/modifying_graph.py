from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal, NotRequired, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter

from awesome_agent.artifacts.repository import ArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
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
    ToolCall,
    ToolChoice,
    ToolChoiceMode,
    ToolResultMessage,
    TransientModelError,
    UserMessage,
)
from awesome_agent.persistence.tool_invocations import (
    DurableToolInvocation,
    ToolInvocationRepository,
)
from awesome_agent.runtime.dispatch import (
    CorruptRuntimeStateError,
    IncompatibleGraphError,
    PermanentExecutionError,
    TransientExecutionError,
)
from awesome_agent.runtime.graphs import (
    MODIFYING_CODING_GRAPH,
    MODIFYING_CODING_VERSION,
)
from awesome_agent.tools.repository import (
    RepositoryRecoveryRequired,
    build_modifying_executor,
    build_modifying_registry,
    canonical_arguments_hash_from_arguments,
    execute_repository_call,
    model_tool_definitions,
    parse_tool_call_arguments,
    repository_tool_effect_metadata,
    tool_invocation_uuid,
)

_MESSAGE_ADAPTER: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)
_SYSTEM_PROMPT = """You are the solo Leader of a modifying coding agent.
Use tools to inspect and edit only the managed Run worktree. Prefer
repo.apply_patch for file changes. Use shell.execute only for allowed
Docker-sandboxed check commands. Before finishing, call repo.diff after the
last write and summarize changed files, commands run, and unverified work.
Do not claim validation passed; Task 10 owns deterministic validation.
"""
_TOOL_RESULT_OFFLOAD_CHARS = 12_000
_TOOL_RESULT_HEAD_CHARS = 8_000
_TOOL_RESULT_TAIL_CHARS = 3_000


class ModifyingAgentState(TypedDict):
    run_id: str
    agent_id: str
    graph_name: str
    graph_version: int
    messages: list[dict[str, Any]]
    continuation: dict[str, Any] | None
    model_turn_count: int
    tool_call_count: int
    successful_writes: int
    final_diff_after_write: bool
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
FaultHook = Callable[[str, ModifyingAgentState], Awaitable[None]]


class ModifyingAgentLoopFailed(PermanentExecutionError):
    pass


class ModifyingCodingGraph:
    def __init__(
        self,
        saver: AsyncPostgresSaver,
        *,
        provider_resolver: ProviderResolver,
        artifact_store: LocalArtifactStore | None = None,
        artifact_repository: ArtifactMetadataRepository | None = None,
        tool_repository: ToolInvocationRepository | None = None,
        max_model_turns: int = 60,
        max_tool_calls: int = 120,
        recursion_limit: int = 256,
        no_progress_turns: int = 8,
        fault_hook: FaultHook | None = None,
    ) -> None:
        self.saver = saver
        self.provider_resolver = provider_resolver
        self.registry = build_modifying_registry(artifact_repository)
        self.executor = build_modifying_executor(self.registry)
        self.artifact_store = artifact_store
        self.artifact_repository = artifact_repository
        self.tool_repository = tool_repository
        self.max_model_turns = max_model_turns
        self.max_tool_calls = max_tool_calls
        self.recursion_limit = recursion_limit
        self.no_progress_turns = no_progress_turns
        self.fault_hook = fault_hook
        self._run: Run | None = None
        self._agent: Agent | None = None
        self._event_sink: EventSink | None = None

        builder = StateGraph(ModifyingAgentState)
        builder.add_node("initialize", self._initialize)
        builder.add_node("model_turn", self._model_turn)
        builder.add_node("execute_tool", self._execute_tool)
        builder.add_node("feedback", self._feedback)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "initialize")
        builder.add_edge("initialize", "model_turn")
        builder.add_conditional_edges(
            "model_turn",
            self._route_turn,
            {
                "tool": "execute_tool",
                "feedback": "feedback",
                "finalize": "finalize",
            },
        )
        builder.add_edge("execute_tool", "model_turn")
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
            name=MODIFYING_CODING_GRAPH,
        )

    async def execute(
        self,
        run: Run,
        agent: Agent,
        *,
        event_sink: EventSink | None = None,
    ) -> tuple[ModifyingAgentState, bool]:
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
            run.graph_name != MODIFYING_CODING_GRAPH
            or run.graph_version != MODIFYING_CODING_VERSION
            or run.intent is not RunIntent.MODIFYING
        ):
            raise IncompatibleGraphError(
                f"Unsupported modifying graph: "
                f"{run.intent.value}/{run.graph_name}@{run.graph_version}"
            )
        if run.graph_thread_id is None:
            raise CorruptRuntimeStateError("Run is missing graph_thread_id.")
        if run.workspace_path is None or not run.workspace_path.is_dir():
            raise CorruptRuntimeStateError("Run workspace is unavailable.")

    async def _initialize(self, state: ModifyingAgentState) -> ModifyingAgentState:
        return {**state, "phase": "initialized"}

    async def _model_turn(self, state: ModifyingAgentState) -> ModifyingAgentState:
        if state["model_turn_count"] >= self.max_model_turns:
            raise ModifyingAgentLoopFailed("Model turn budget exhausted.")
        agent = self._require_agent()
        messages = [
            _MESSAGE_ADAPTER.validate_python(item) for item in state["messages"]
        ]
        next_count = state["model_turn_count"] + 1
        force_final = next_count >= self.max_model_turns
        continuation = (
            ContinuationState.model_validate(state["continuation"])
            if state["continuation"] is not None
            else None
        )
        provider = self.provider_resolver(agent.model)
        try:
            turn = await provider.complete(
                ModelRequest(
                    messages=messages,
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
            raise ModifyingAgentLoopFailed(str(error)) from error
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
        state: ModifyingAgentState,
    ) -> Literal["tool", "feedback", "finalize"]:
        turn = ModelTurn.model_validate(state["last_turn"])
        if turn.assistant.tool_calls:
            if state["force_final"]:
                return "feedback"
            return "tool"
        if (
            turn.stop_reason is StopReason.COMPLETED
            and bool(turn.assistant.content.strip())
            and state["successful_writes"] > 0
            and state["final_diff_after_write"]
        ):
            return "finalize"
        return "feedback"

    async def _execute_tool(
        self,
        state: ModifyingAgentState,
    ) -> ModifyingAgentState:
        turn = ModelTurn.model_validate(state["last_turn"])
        calls = turn.assistant.tool_calls
        if not calls:
            return state
        if state["tool_call_count"] + len(calls) > self.max_tool_calls:
            return {
                **state,
                "messages": [
                    *state["messages"],
                    turn.assistant.model_dump(mode="json"),
                    SystemMessage(
                        content=(
                            "The tool-call budget is exhausted. Produce the best "
                            "summary of completed, unvalidated work."
                        )
                    ).model_dump(mode="json"),
                ],
                "force_final": True,
                "phase": "tool_budget_exhausted",
            }
        ordered_results = []
        successful_writes = state["successful_writes"]
        final_diff_after_write = state["final_diff_after_write"]
        fingerprints: list[str] = []
        for call in calls:
            try:
                result = await self._execute_durable_tool_call(call)
            except RepositoryRecoveryRequired as error:
                raise CorruptRuntimeStateError(str(error)) from error
            result = await self._offload_result_if_needed(call.call_id, result)
            if call.name == "repo.apply_patch" and not result.is_error:
                successful_writes += 1
                final_diff_after_write = False
            if call.name == "repo.diff" and not result.is_error and successful_writes:
                final_diff_after_write = True
            fingerprint = hashlib.sha256(
                f"{call.name}\0{call.arguments_json}\0{result.content}".encode()
            ).hexdigest()
            fingerprints.append(fingerprint)
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
            ordered_results.append(result)
        prior = set(state["progress_fingerprints"])
        has_progress = any(fingerprint not in prior for fingerprint in fingerprints)
        stagnant = 0 if has_progress else state["stagnant_turns"] + 1
        messages = [
            *state["messages"],
            turn.assistant.model_dump(mode="json"),
            *(result.model_dump(mode="json") for result in ordered_results),
        ]
        if stagnant >= self.no_progress_turns:
            messages.append(
                SystemMessage(
                    content=(
                        "You are repeating prior actions without progress. Change "
                        "strategy, inspect the diff, or summarize why progress is "
                        "blocked."
                    )
                ).model_dump(mode="json")
            )
        updated: ModifyingAgentState = {
            **state,
            "messages": messages,
            "tool_call_count": state["tool_call_count"] + len(calls),
            "successful_writes": successful_writes,
            "final_diff_after_write": final_diff_after_write,
            "progress_fingerprints": [
                *state["progress_fingerprints"],
                *fingerprints,
            ],
            "stagnant_turns": stagnant,
            "phase": "tools_completed",
        }
        if self.fault_hook is not None:
            await self.fault_hook("execute_tool", updated)
        return updated

    async def _execute_durable_tool_call(self, call: ToolCall) -> ToolResultMessage:
        if self.tool_repository is None:
            return await self._execute_tool_call(call)
        run = self._require_run()
        agent = self._require_agent()
        workspace = cast(Any, run.workspace_path)
        arguments = parse_tool_call_arguments(call)
        spec, _ = self.registry.resolve(call.name)
        arguments_fingerprint = canonical_arguments_hash_from_arguments(arguments)
        idempotency_key = _idempotency_key(
            run_id=str(run.id),
            agent_id=str(agent.id),
            tool_name=call.name,
            tool_version=spec.version,
            arguments_hash=arguments_fingerprint,
            workspace=str(workspace),
        )
        existing = await self.tool_repository.get_by_idempotency_key(
            run.id,
            idempotency_key,
        )
        if existing is not None:
            if existing.arguments_hash != arguments_fingerprint:
                raise CorruptRuntimeStateError(
                    "Tool invocation idempotency collision changed arguments."
                )
            if existing.status in {"completed", "failed"}:
                if existing.result_content is None:
                    raise CorruptRuntimeStateError(
                        "Completed tool invocation has no durable result."
                    )
                return ToolResultMessage(
                    call_id=call.call_id,
                    content=existing.result_content,
                    is_error=existing.result_is_error,
                )
            if call.name == "shell.execute":
                raise CorruptRuntimeStateError(
                    "Shell execution completion is unknown after restart."
                )
            if call.name != "repo.apply_patch":
                raise CorruptRuntimeStateError(
                    f"Tool invocation {existing.id} stopped before completion."
                )

        now = datetime.now(UTC)
        if existing is not None:
            invocation = _copy_invocation(existing, updated_at=now)
        else:
            path_refs, preimage_hashes = repository_tool_effect_metadata(
                call.name,
                arguments,
                workspace=workspace,
            )
            invocation = DurableToolInvocation(
                id=tool_invocation_uuid(call.call_id),
                run_id=run.id,
                agent_id=agent.id,
                tool_name=call.name,
                tool_version=spec.version,
                status="started",
                idempotency_key=idempotency_key,
                arguments_hash=arguments_fingerprint,
                risk_level=spec.risk_level.value,
                path_refs=path_refs,
                preimage_hashes=preimage_hashes,
                started_at=now,
                updated_at=now,
            )
        await self.tool_repository.upsert(invocation)
        try:
            result = await self._execute_tool_call(call)
        except RepositoryRecoveryRequired as error:
            await self.tool_repository.upsert(
                _copy_invocation(
                    invocation,
                    status="recovery_required",
                    error=str(error),
                    updated_at=datetime.now(UTC),
                )
            )
            raise
        completed_at = datetime.now(UTC)
        expected_postimage_hashes = _extract_postimage_hashes(result.content)
        await self.tool_repository.upsert(
            _copy_invocation(
                invocation,
                status="failed" if result.is_error else "completed",
                expected_postimage_hashes=expected_postimage_hashes,
                result_summary=result.content[:500],
                result_content=result.content,
                result_is_error=result.is_error,
                error=result.content[:500] if result.is_error else None,
                completed_at=completed_at,
                updated_at=completed_at,
            )
        )
        return result

    async def _execute_tool_call(self, call: ToolCall) -> ToolResultMessage:
        run = self._require_run()
        agent = self._require_agent()
        return await execute_repository_call(
            self.executor,
            call,
            workspace=cast(Any, run.workspace_path),
            agent_id=agent.id,
            capabilities={
                "repository:read",
                "repository:write",
                "shell:execute",
                "artifact:read",
            },
        )

    async def _feedback(self, state: ModifyingAgentState) -> ModifyingAgentState:
        turn = ModelTurn.model_validate(state["last_turn"])
        if state["force_final"]:
            if (
                turn.assistant.content.strip()
                and state["successful_writes"] > 0
                and state["final_diff_after_write"]
            ):
                return {
                    **state,
                    "last_turn": turn.model_copy(
                        update={"stop_reason": StopReason.COMPLETED}
                    ).model_dump(mode="json"),
                    "phase": "forced_completion",
                }
            raise ModifyingAgentLoopFailed(
                "The final no-tool turn did not produce a supported modifying result."
            )
        missing = []
        if state["successful_writes"] == 0:
            missing.append("make an actual patch or explain a no-change block")
        if not state["final_diff_after_write"]:
            missing.append("call repo.diff after the last write")
        return {
            **state,
            "messages": [
                *state["messages"],
                turn.assistant.model_dump(mode="json"),
                SystemMessage(
                    content=(
                        "Do not finish yet. Required before completion: "
                        + "; ".join(missing)
                        + "."
                    )
                ).model_dump(mode="json"),
            ],
            "stagnant_turns": state["stagnant_turns"] + 1,
            "phase": "completion_rejected",
        }

    async def _finalize(self, state: ModifyingAgentState) -> ModifyingAgentState:
        turn = ModelTurn.model_validate(state["last_turn"])
        answer = turn.assistant.content.strip()
        await self._emit(
            EventType.MESSAGE_CREATED,
            {
                "role": "assistant",
                "content": answer[:32768],
                "final": True,
                "validation_complete": False,
            },
            "final-answer",
        )
        return {
            **state,
            "phase": "completed",
            "final_answer": answer[:32768],
            "result_summary": (
                f"Modifying repository task produced unvalidated changes after "
                f"{state['model_turn_count']} model turn(s), "
                f"{state['tool_call_count']} tool call(s), and "
                f"{state['successful_writes']} write(s)."
            ),
        }

    async def _offload_result_if_needed(
        self,
        call_id: str,
        result: ToolResultMessage,
    ) -> ToolResultMessage:
        if (
            self.artifact_store is None
            or self.artifact_repository is None
            or len(result.content) <= _TOOL_RESULT_OFFLOAD_CHARS
        ):
            return result
        run = self._require_run()
        agent = self._require_agent()
        metadata = self.artifact_store.write(
            run_id=run.id,
            agent_id=agent.id,
            artifact_type="tool-output",
            filename=f"{call_id}.json",
            content=result.content.encode("utf-8"),
            mime_type="application/json",
            summary=f"Large tool output for {call_id}",
        )
        await self.artifact_repository.record(metadata)
        head = result.content[:_TOOL_RESULT_HEAD_CHARS]
        tail = result.content[-_TOOL_RESULT_TAIL_CHARS:]
        return result.model_copy(
            update={
                "content": (
                    f"{head}\n...[tool output offloaded to artifact "
                    f"{metadata.id}; {len(result.content)} characters]...\n{tail}"
                ),
                "artifact_refs": [str(metadata.id)],
            }
        )

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


def _initial_state(run: Run, agent: Agent) -> ModifyingAgentState:
    return {
        "run_id": str(run.id),
        "agent_id": str(agent.id),
        "graph_name": MODIFYING_CODING_GRAPH,
        "graph_version": MODIFYING_CODING_VERSION,
        "messages": [
            SystemMessage(content=_SYSTEM_PROMPT).model_dump(mode="json"),
            UserMessage(content=run.goal).model_dump(mode="json"),
        ],
        "continuation": None,
        "model_turn_count": 0,
        "tool_call_count": 0,
        "successful_writes": 0,
        "final_diff_after_write": False,
        "progress_fingerprints": [],
        "stagnant_turns": 0,
        "phase": "created",
        "force_final": False,
    }


def _state(value: object) -> ModifyingAgentState:
    if not isinstance(value, dict):
        raise CorruptRuntimeStateError("Modifying graph returned invalid state.")
    required = {
        "run_id",
        "agent_id",
        "graph_name",
        "graph_version",
        "messages",
        "model_turn_count",
        "tool_call_count",
        "successful_writes",
        "final_diff_after_write",
        "phase",
    }
    if not required.issubset(value):
        raise CorruptRuntimeStateError("Modifying graph state is incomplete.")
    return cast(ModifyingAgentState, value)


def _idempotency_key(
    *,
    run_id: str,
    agent_id: str,
    tool_name: str,
    tool_version: str,
    arguments_hash: str,
    workspace: str,
) -> str:
    raw = "\0".join(
        [run_id, agent_id, tool_name, tool_version, arguments_hash, workspace]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _extract_postimage_hashes(content: str) -> dict[str, str]:
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    postimage_hashes = decoded.get("postimage_hashes")
    if not isinstance(postimage_hashes, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in postimage_hashes.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _copy_invocation(
    invocation: DurableToolInvocation,
    **updates: object,
) -> DurableToolInvocation:
    return replace(invocation, **updates)  # type: ignore[arg-type]
