from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from time import monotonic
from typing import Any, Literal, NotRequired, TypedDict, cast
from uuid import UUID

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
    SystemMessage,
    ToolCall,
    ToolChoice,
    ToolChoiceMode,
    ToolResultMessage,
    TransientModelError,
    UserMessage,
)
from awesome_agent.observability.facade import ObservabilityFacade
from awesome_agent.persistence.approvals import ApprovalRepository, DurableApproval
from awesome_agent.persistence.budget import BudgetRepository
from awesome_agent.persistence.tool_invocations import (
    DurableToolInvocation,
    ToolInvocationRepository,
)
from awesome_agent.persistence.validation import (
    ValidationReportWithGates,
    ValidationRepository,
)
from awesome_agent.runtime.agent_loop.modifying import ModifyingAgentLoop
from awesome_agent.runtime.agent_loop.modifying_middleware import (
    ModifyingApprovalMiddleware,
    ModifyingArtifactMiddleware,
    ModifyingBudgetExhausted,
    ModifyingBudgetMiddleware,
    ModifyingContextMiddleware,
    ModifyingEvidenceMiddleware,
    ModifyingFinalizationMiddleware,
    ModifyingToolMiddleware,
    ModifyingValidationMiddleware,
    approval_id_from_interrupt_value,
    approval_interrupt_payload,
    elapsed_ms,
    extract_postimage_hashes,
    idempotency_key_for_tool_invocation,
    modifying_ledger_to_state,
    resume_approval_id,
    tool_error_result,
    validation_failure_is_reworkable,
    validation_report_snapshot,
    workspace_fingerprint,
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
    MODIFYING_CODING_ROUTE,
)
from awesome_agent.runtime.validation.config import load_validation_config
from awesome_agent.runtime.validation.detection import detect_validation_plan
from awesome_agent.runtime.validation.executor import execute_validation_plan
from awesome_agent.runtime.validation.models import ValidationPlan
from awesome_agent.tools.repository import (
    build_modifying_executor,
    build_modifying_registry,
    model_tool_definitions,
)

_MESSAGE_ADAPTER: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)
_SYSTEM_PROMPT = """You are the solo Leader of a modifying coding agent.
Use tools to inspect and edit only the managed Run worktree. Prefer
repo.apply_patch for file changes. Use shell.execute only for allowed
Docker-sandboxed check commands. Before finishing, call repo.diff after the
last write and summarize changed files, commands run, and unverified work.
Do not claim validation passed; Task 10 owns deterministic validation.
"""


class ModifyingAgentState(TypedDict):
    run_id: str
    agent_id: str
    runtime_route: str
    messages: list[dict[str, Any]]
    continuation: dict[str, Any] | None
    model_turn_count: int
    tool_call_count: int
    successful_writes: int
    final_diff_after_write: bool
    progress_fingerprints: list[str]
    stagnant_turns: int
    validation_rework_count: int
    validation_reports: list[dict[str, Any]]
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
FaultHook = Callable[[str, ModifyingAgentState], Awaitable[None]]
ValidationPlanResolver = Callable[[Path], ValidationPlan | None]
ValidationRunner = Callable[
    [ValidationPlan, Run, Agent],
    Awaitable[ValidationReportWithGates],
]


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
        approval_repository: ApprovalRepository | None = None,
        validation_repository: ValidationRepository | None = None,
        validation_plan_resolver: ValidationPlanResolver | None = None,
        validation_runner: ValidationRunner | None = None,
        approval_default_expiry: timedelta = timedelta(minutes=60),
        max_model_turns: int = 60,
        max_tool_calls: int = 120,
        recursion_limit: int = 256,
        no_progress_turns: int = 8,
        fault_hook: FaultHook | None = None,
        context_manager: ContextManager | None = None,
        budget_repository: BudgetRepository | None = None,
        budget_policy: BudgetPolicy | None = None,
        observability: ObservabilityFacade | None = None,
    ) -> None:
        self.saver = saver
        self.provider_resolver = provider_resolver
        self.registry = build_modifying_registry(artifact_repository)
        self.executor = build_modifying_executor(self.registry)
        self.artifact_store = artifact_store
        self.artifact_repository = artifact_repository
        self.tool_repository = tool_repository
        self.approval_repository = approval_repository
        self.validation_repository = validation_repository
        self.validation_plan_resolver = (
            validation_plan_resolver or _resolve_validation_plan
        )
        self.validation_runner = validation_runner or self._run_validation
        self.approval_default_expiry = approval_default_expiry
        self.max_model_turns = max_model_turns
        self.max_tool_calls = max_tool_calls
        self.recursion_limit = recursion_limit
        self.no_progress_turns = no_progress_turns
        self.fault_hook = fault_hook
        self.context_manager = context_manager
        self.budget_repository = budget_repository
        self.budget_policy = budget_policy
        self.agent_loop = ModifyingAgentLoop(observability=observability)
        self.context_middleware = ModifyingContextMiddleware(
            context_manager=context_manager,
            budget_repository=budget_repository,
            budget_policy=budget_policy,
            runtime_route=MODIFYING_CODING_ROUTE,
        )
        self.budget_middleware = ModifyingBudgetMiddleware(
            budget_repository=budget_repository,
            budget_policy=budget_policy,
            emit=self._emit,
        )
        self.artifact_middleware = ModifyingArtifactMiddleware(
            artifact_store=artifact_store,
            artifact_repository=artifact_repository,
        )
        self.approval_middleware = ModifyingApprovalMiddleware(
            approval_repository=approval_repository,
            tool_repository=tool_repository,
            approval_default_expiry=approval_default_expiry,
            emit=self._emit,
        )
        self.tool_middleware = ModifyingToolMiddleware(
            registry=self.registry,
            executor=self.executor,
            tool_repository=tool_repository,
            approval_middleware=self.approval_middleware,
            artifact_middleware=self.artifact_middleware,
            max_tool_calls=max_tool_calls,
            no_progress_turns=no_progress_turns,
            fault_hook=fault_hook,
            emit=self._emit,
        )
        self.evidence_middleware = ModifyingEvidenceMiddleware(
            failure_factory=ModifyingAgentLoopFailed,
        )
        self.validation_middleware = ModifyingValidationMiddleware(
            validation_plan_resolver=self.validation_plan_resolver,
            validation_runner=self.validation_runner,
            emit=self._emit,
            failure_factory=ModifyingAgentLoopFailed,
        )
        self.finalization_middleware = ModifyingFinalizationMiddleware(
            emit=self._emit,
        )
        self._run: Run | None = None
        self._agent: Agent | None = None
        self._event_sink: EventSink | None = None

        builder = StateGraph(ModifyingAgentState)
        builder.add_node("initialize", self._initialize)
        builder.add_node("model_turn", self._model_turn)
        builder.add_node("execute_tool", self._execute_tool)
        builder.add_node("feedback", self._feedback)
        builder.add_node("validate", self._validate)
        builder.add_node("validation_feedback", self._validation_feedback)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "initialize")
        builder.add_edge("initialize", "model_turn")
        builder.add_conditional_edges(
            "model_turn",
            self._route_turn,
            {
                "tool": "execute_tool",
                "feedback": "feedback",
                "validate": "validate",
            },
        )
        builder.add_edge("execute_tool", "model_turn")
        builder.add_conditional_edges(
            "feedback",
            lambda state: (
                "validate" if state["phase"] == "forced_completion" else "model_turn"
            ),
            {
                "validate": "validate",
                "model_turn": "model_turn",
            },
        )
        builder.add_conditional_edges(
            "validate",
            lambda state: (
                "finalize"
                if state["phase"] == "validation_passed"
                else "validation_feedback"
            ),
            {
                "finalize": "finalize",
                "validation_feedback": "validation_feedback",
            },
        )
        builder.add_edge("validation_feedback", "model_turn")
        builder.add_edge("finalize", END)
        self.graph = builder.compile(
            checkpointer=saver,
            name=MODIFYING_CODING_ROUTE,
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
                self._raise_if_interrupted(result)
                return _state(result), False
            snapshot = await self.graph.aget_state(config)
            if not snapshot.next:
                return _state(snapshot.values), True
            command = await self._resume_command(snapshot)
            result = await self.graph.ainvoke(command, config, durability="sync")
            self._raise_if_interrupted(result)
            return _state(result), True
        finally:
            self._run = None
            self._agent = None
            self._event_sink = None

    def _validate_run(self, run: Run) -> None:
        if (
            run.runtime_route != MODIFYING_CODING_ROUTE
            or run.intent is not RunIntent.MODIFYING
        ):
            raise IncompatibleGraphError(
                f"Unsupported modifying graph: {run.intent.value}/{run.runtime_route}"
            )
        if run.graph_thread_id is None:
            raise CorruptRuntimeStateError("Run is missing graph_thread_id.")
        if run.workspace_path is None or not run.workspace_path.is_dir():
            raise CorruptRuntimeStateError("Run workspace is unavailable.")

    async def _initialize(self, state: ModifyingAgentState) -> ModifyingAgentState:
        run = self._require_run()
        agent = self._require_agent()
        messages = [
            _MESSAGE_ADAPTER.validate_python(item) for item in state["messages"]
        ]
        return await self.agent_loop.before_agent(
            state,
            run=run,
            agent=agent,
            messages=messages,
            handler=self._initialize_impl,
        )

    async def _initialize_impl(
        self,
        state: ModifyingAgentState,
    ) -> ModifyingAgentState:
        return {**state, "phase": "initialized"}

    async def _model_turn(self, state: ModifyingAgentState) -> ModifyingAgentState:
        run = self._require_run()
        agent = self._require_agent()
        messages = [
            _MESSAGE_ADAPTER.validate_python(item) for item in state["messages"]
        ]
        state = await self.agent_loop.before_model(
            state,
            run=run,
            agent=agent,
            messages=messages,
            handler=lambda current: self.agent_loop.wrap_model_call(
                current,
                run=run,
                agent=agent,
                messages=messages,
                handler=self._model_turn_impl,
            ),
        )
        return await self.agent_loop.after_model(
            state,
            run=run,
            agent=agent,
            messages=[
                _MESSAGE_ADAPTER.validate_python(item) for item in state["messages"]
            ],
            handler=_identity_state,
        )

    async def _model_turn_impl(
        self,
        state: ModifyingAgentState,
    ) -> ModifyingAgentState:
        if state["model_turn_count"] >= self.max_model_turns:
            raise ModifyingAgentLoopFailed("Model turn budget exhausted.")
        run = self._require_run()
        agent = self._require_agent()
        messages = [
            _MESSAGE_ADAPTER.validate_python(item) for item in state["messages"]
        ]
        next_count = state["model_turn_count"] + 1
        force_final = state["force_final"] or next_count >= self.max_model_turns
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
                        "The prompt is at the hard context limit. Do not request "
                        "tools. If a final diff is already present, summarize the "
                        "completed changes for validation; otherwise provide a "
                        "bounded status of incomplete work and remaining risk."
                    )
                )
            )
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
        except ModifyingBudgetExhausted as error:
            raise ModifyingAgentLoopFailed(str(error)) from error
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
            raise ModifyingAgentLoopFailed(str(error)) from error
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
            "budget_ledger": modifying_ledger_to_state(ledger),
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
        state: ModifyingAgentState,
    ) -> Literal["tool", "feedback", "validate"]:
        turn = ModelTurn.model_validate(state["last_turn"])
        return cast(
            Literal["tool", "feedback", "validate"],
            self.evidence_middleware.route_turn(
                turn=turn,
                force_final=state["force_final"],
                successful_writes=state["successful_writes"],
                final_diff_after_write=state["final_diff_after_write"],
            ),
        )

    async def _execute_tool(
        self,
        state: ModifyingAgentState,
    ) -> ModifyingAgentState:
        run = self._require_run()
        agent = self._require_agent()
        messages = [
            _MESSAGE_ADAPTER.validate_python(item) for item in state["messages"]
        ]
        return await self.agent_loop.wrap_tool_call(
            state,
            run=run,
            agent=agent,
            messages=messages,
            handler=self._execute_tool_impl,
        )

    async def _execute_tool_impl(
        self,
        state: ModifyingAgentState,
    ) -> ModifyingAgentState:
        turn = ModelTurn.model_validate(state["last_turn"])
        return cast(
            ModifyingAgentState,
            await self.tool_middleware.execute_turn(
                state=cast(dict[str, Any], state),
                turn_assistant=turn.assistant,
                calls=turn.assistant.tool_calls,
                run=self._require_run(),
                agent=self._require_agent(),
            ),
        )

    async def _execute_durable_tool_call(self, call: ToolCall) -> ToolResultMessage:
        run = self._require_run()
        agent = self._require_agent()
        return await self.tool_middleware.execute_durable_tool_call(
            call,
            run=run,
            agent=agent,
        )

    async def _interrupt_for_approval(
        self,
        *,
        call: ToolCall,
        invocation: DurableToolInvocation,
        arguments: dict[str, Any],
        arguments_hash: str,
        workspace: Path,
        tool_version: str,
        risk_level: str,
    ) -> ToolResultMessage:
        return await self.approval_middleware.interrupt_for_approval(
            call=call,
            invocation=invocation,
            arguments=arguments,
            arguments_hash=arguments_hash,
            workspace=workspace,
            tool_version=tool_version,
            risk_level=risk_level,
            run=self._require_run(),
            agent=self._require_agent(),
            execute_tool_call=lambda resumed_call, *, approval_granted=False: (
                self._execute_tool_call(
                    resumed_call,
                    approval_granted=approval_granted,
                )
            ),
        )

    async def _upsert_approval(
        self,
        *,
        call: ToolCall,
        invocation: DurableToolInvocation,
        arguments: dict[str, Any],
        arguments_hash: str,
        workspace: Path,
        tool_version: str,
        risk_level: str,
    ) -> DurableApproval:
        run = self._require_run()
        agent = self._require_agent()
        return await self.approval_middleware.upsert_approval(
            call=call,
            invocation=invocation,
            arguments=arguments,
            arguments_hash=arguments_hash,
            workspace=workspace,
            tool_version=tool_version,
            risk_level=risk_level,
            run=run,
            agent=agent,
        )

    async def _resume_approved_tool_call(
        self,
        *,
        call: ToolCall,
        approval_id: UUID,
        expected_arguments_hash: str,
        expected_tool_version: str,
        expected_workspace_fingerprint: str,
        expected_capabilities: list[str],
    ) -> ToolResultMessage:
        return await self.approval_middleware.resume_approved_tool_call(
            call=call,
            approval_id=approval_id,
            expected_arguments_hash=expected_arguments_hash,
            expected_tool_version=expected_tool_version,
            expected_workspace_fingerprint=expected_workspace_fingerprint,
            expected_capabilities=expected_capabilities,
            execute_tool_call=lambda resumed_call, *, approval_granted=False: (
                self._execute_tool_call(
                    resumed_call,
                    approval_granted=approval_granted,
                )
            ),
        )

    async def _execute_tool_call(
        self,
        call: ToolCall,
        *,
        approval_granted: bool = False,
    ) -> ToolResultMessage:
        run = self._require_run()
        agent = self._require_agent()
        return await self.tool_middleware.execute_tool_call(
            call,
            run=run,
            agent=agent,
            approval_granted=approval_granted,
        )

    async def _resume_command(self, snapshot: Any) -> Any:
        return await self.approval_middleware.resume_command(snapshot)

    def _raise_if_interrupted(self, result: object) -> None:
        self.approval_middleware.raise_if_interrupted(result)

    async def _feedback(self, state: ModifyingAgentState) -> ModifyingAgentState:
        turn = ModelTurn.model_validate(state["last_turn"])
        return cast(
            ModifyingAgentState,
            self.evidence_middleware.feedback_state(
                state=cast(dict[str, Any], state),
                turn=turn,
            ),
        )

    async def _validate(self, state: ModifyingAgentState) -> ModifyingAgentState:
        run = self._require_run()
        agent = self._require_agent()
        workspace = cast(Path, run.workspace_path)
        return cast(
            ModifyingAgentState,
            await self.validation_middleware.validate(
                state=cast(dict[str, Any], state),
                run=run,
                agent=agent,
                workspace=workspace,
            ),
        )

    async def _validation_feedback(
        self,
        state: ModifyingAgentState,
    ) -> ModifyingAgentState:
        turn = ModelTurn.model_validate(state["last_turn"])
        return cast(
            ModifyingAgentState,
            self.validation_middleware.validation_feedback(
                state=cast(dict[str, Any], state),
                turn=turn,
            ),
        )

    async def _finalize(self, state: ModifyingAgentState) -> ModifyingAgentState:
        run = self._require_run()
        agent = self._require_agent()
        messages = [
            _MESSAGE_ADAPTER.validate_python(item) for item in state["messages"]
        ]
        return await self.agent_loop.after_agent(
            state,
            run=run,
            agent=agent,
            messages=messages,
            handler=self._finalize_impl,
        )

    async def _finalize_impl(
        self,
        state: ModifyingAgentState,
    ) -> ModifyingAgentState:
        turn = ModelTurn.model_validate(state["last_turn"])
        return cast(
            ModifyingAgentState,
            await self.finalization_middleware.finalize(
                state=cast(dict[str, Any], state),
                turn=turn,
            ),
        )

    async def _offload_result_if_needed(
        self,
        call_id: str,
        result: ToolResultMessage,
    ) -> ToolResultMessage:
        return await self.artifact_middleware.offload_result_if_needed(
            call_id=call_id,
            result=result,
            run=self._require_run(),
            agent=self._require_agent(),
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

    def _require_approval_repository(self) -> ApprovalRepository:
        if self.approval_repository is None:
            raise CorruptRuntimeStateError("Approval repository is not configured.")
        return self.approval_repository

    async def _run_validation(
        self,
        plan: ValidationPlan,
        run: Run,
        agent: Agent,
    ) -> ValidationReportWithGates:
        return await execute_validation_plan(
            plan,
            run_id=run.id,
            agent_id=agent.id,
            workspace=cast(Path, run.workspace_path),
            repository=self.validation_repository,
        )


def _initial_state(run: Run, agent: Agent) -> ModifyingAgentState:
    return {
        "run_id": str(run.id),
        "agent_id": str(agent.id),
        "runtime_route": MODIFYING_CODING_ROUTE,
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
        "validation_rework_count": 0,
        "validation_reports": [],
        "phase": "created",
        "force_final": False,
        "rolling_summary": "",
        "budget_ledger": {},
        "context_artifact_refs": [],
    }


async def _identity_state(state: ModifyingAgentState) -> ModifyingAgentState:
    return state


def _state(value: object) -> ModifyingAgentState:
    if not isinstance(value, dict):
        raise CorruptRuntimeStateError("Modifying graph returned invalid state.")
    required = {
        "run_id",
        "agent_id",
        "runtime_route",
        "messages",
        "model_turn_count",
        "tool_call_count",
        "successful_writes",
        "final_diff_after_write",
        "validation_rework_count",
        "validation_reports",
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
    return idempotency_key_for_tool_invocation(
        run_id=run_id,
        agent_id=agent_id,
        tool_name=tool_name,
        tool_version=tool_version,
        arguments_hash=arguments_hash,
        workspace=workspace,
    )


def _extract_postimage_hashes(content: str) -> dict[str, str]:
    return extract_postimage_hashes(content)


def _approval_interrupt_payload(approval: DurableApproval) -> dict[str, object]:
    return approval_interrupt_payload(approval)


def _approval_id_from_interrupt_value(value: object) -> UUID:
    return approval_id_from_interrupt_value(value)


def _resume_approval_id(value: object) -> UUID:
    return resume_approval_id(value)


def _tool_error_result(
    call: ToolCall,
    status: str,
    message: str,
) -> ToolResultMessage:
    return tool_error_result(call, status, message)


async def _workspace_fingerprint(workspace: Path) -> str:
    return await workspace_fingerprint(workspace)


def _copy_invocation(
    invocation: DurableToolInvocation,
    **updates: object,
) -> DurableToolInvocation:
    return replace(invocation, **updates)  # type: ignore[arg-type]


def _resolve_validation_plan(workspace: Path) -> ValidationPlan | None:
    return load_validation_config(workspace) or detect_validation_plan(workspace)


def _validation_report_snapshot(report: ValidationReportWithGates) -> dict[str, Any]:
    return validation_report_snapshot(report)


def _validation_failure_is_reworkable(report: ValidationReportWithGates) -> bool:
    return validation_failure_is_reworkable(report)


def _elapsed_ms(started: float) -> int:
    return elapsed_ms(started)
