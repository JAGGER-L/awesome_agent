from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any, Literal, NotRequired, TypedDict, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import TypeAdapter

from awesome_agent.artifacts.repository import ArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import ApprovalStatus, EventType, RunIntent
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
from awesome_agent.persistence.approvals import ApprovalRepository, DurableApproval
from awesome_agent.persistence.budget import (
    BudgetRepository,
    ContextCompactionRecord,
    RunBudgetLedgerRecord,
)
from awesome_agent.persistence.tool_invocations import (
    DurableToolInvocation,
    ToolInvocationRepository,
)
from awesome_agent.persistence.validation import (
    ValidationReportWithGates,
    ValidationRepository,
)
from awesome_agent.runtime.budget import (
    BudgetDecision,
    BudgetLedger,
    BudgetPolicy,
    TokenUsageDelta,
    estimate_messages_tokens,
    evaluate_budget,
)
from awesome_agent.runtime.context import ContextManager, ContextPolicy, PreparedContext
from awesome_agent.runtime.dispatch import (
    ApprovalInterrupt,
    CorruptRuntimeStateError,
    IncompatibleGraphError,
    PermanentExecutionError,
    TransientExecutionError,
)
from awesome_agent.runtime.graphs import (
    MODIFYING_CODING_GRAPH,
    MODIFYING_CODING_VERSION,
)
from awesome_agent.runtime.validation.config import load_validation_config
from awesome_agent.runtime.validation.detection import detect_validation_plan
from awesome_agent.runtime.validation.executor import execute_validation_plan
from awesome_agent.runtime.validation.models import ValidationPlan
from awesome_agent.tools.models import ApprovalRequired, ToolDenied
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
        run = self._require_run()
        agent = self._require_agent()
        messages = [
            _MESSAGE_ADAPTER.validate_python(item) for item in state["messages"]
        ]
        next_count = state["model_turn_count"] + 1
        force_final = state["force_final"] or next_count >= self.max_model_turns
        ledger = await self._load_budget_ledger(run.id, state)
        prepared = await self._prepare_context(
            run=run,
            agent=agent,
            messages=messages,
            rolling_summary=state.get("rolling_summary", ""),
        )
        checkpoint_messages = prepared.request_messages if prepared else messages
        request_messages = list(checkpoint_messages)
        if prepared is not None and prepared.compacted:
            await self._record_context_compaction(run, agent, prepared)
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
        ledger = await self._evaluate_budget_before_model_call(
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
        await self._persist_budget_ledger(run.id, ledger)
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
            "budget_ledger": _ledger_to_state(ledger),
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
            return "validate"
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
            started = monotonic()
            try:
                result = await self._execute_durable_tool_call(call)
            except RepositoryRecoveryRequired as error:
                raise CorruptRuntimeStateError(str(error)) from error
            except ToolDenied as error:
                result = _tool_error_result(call, "denied", str(error))
            latency_ms = _elapsed_ms(started)
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
                    "sandbox": "docker" if call.name == "shell.execute" else "",
                    "latency_ms": latency_ms,
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
            if existing.status == "approval_pending":
                invocation = _copy_invocation(existing, updated_at=datetime.now(UTC))
            elif call.name == "shell.execute":
                raise CorruptRuntimeStateError(
                    "Shell execution completion is unknown after restart."
                )
            elif call.name != "repo.apply_patch":
                raise CorruptRuntimeStateError(
                    f"Tool invocation {existing.id} stopped before completion."
                )
            else:
                invocation = _copy_invocation(existing, updated_at=datetime.now(UTC))
        else:
            now = datetime.now(UTC)
            path_refs, preimage_hashes = repository_tool_effect_metadata(
                call.name,
                arguments,
                workspace=workspace,
            )
            invocation = DurableToolInvocation(
                id=tool_invocation_uuid(f"{run.id}:{call.call_id}"),
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
        except ToolDenied as error:
            result = _tool_error_result(call, "denied", str(error))
        except ApprovalRequired:
            result = await self._interrupt_for_approval(
                call=call,
                invocation=invocation,
                arguments=arguments,
                arguments_hash=arguments_fingerprint,
                workspace=workspace,
                tool_version=spec.version,
                risk_level=spec.risk_level.value,
            )
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
        if self.approval_repository is None:
            raise CorruptRuntimeStateError("Approval repository is not configured.")
        approval = await self._upsert_approval(
            call=call,
            invocation=invocation,
            arguments=arguments,
            arguments_hash=arguments_hash,
            workspace=workspace,
            tool_version=tool_version,
            risk_level=risk_level,
        )
        if self.tool_repository is not None:
            await self.tool_repository.upsert(
                _copy_invocation(
                    invocation,
                    status="approval_pending",
                    updated_at=datetime.now(UTC),
                )
            )
        payload = _approval_interrupt_payload(approval)
        await self._emit(
            EventType.APPROVAL_REQUESTED,
            {
                **payload,
                "reason": "waiting_approval",
                "agent_id": str(self._require_agent().id),
            },
            f"approval:{approval.tool_call_id}",
        )
        resume = interrupt(payload)
        return await self._resume_approved_tool_call(
            call=call,
            approval_id=_resume_approval_id(resume),
            expected_arguments_hash=arguments_hash,
            expected_tool_version=tool_version,
            expected_workspace_fingerprint=approval.workspace_fingerprint,
            expected_capabilities=approval.capabilities,
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
        repository = self._require_approval_repository()
        run = self._require_run()
        agent = self._require_agent()
        existing = await repository.get_by_call(run.id, call.call_id)
        now = datetime.now(UTC)
        approval = DurableApproval(
            id=(existing.id if existing is not None else invocation.id),
            run_id=run.id,
            agent_id=agent.id,
            tool_invocation_id=invocation.id,
            tool_call_id=call.call_id,
            tool_name=call.name,
            tool_version=tool_version,
            canonical_arguments=arguments,
            arguments_hash=arguments_hash,
            workspace_path=str(workspace),
            workspace_fingerprint=await _workspace_fingerprint(workspace),
            capabilities=[
                "artifact:read",
                "repository:read",
                "repository:write",
                "shell:execute",
            ],
            risk_level=risk_level,
            expires_at=(
                existing.expires_at
                if existing is not None
                else now + self.approval_default_expiry
            ),
            status=(
                existing.status if existing is not None else ApprovalStatus.PENDING
            ),
            created_at=(existing.created_at if existing is not None else now),
            updated_at=now,
        )
        return await repository.upsert(approval)

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
        approval = await self._require_approval_repository().get(approval_id)
        if approval.tool_call_id != call.call_id:
            raise CorruptRuntimeStateError("Approval resume tool call mismatch.")
        if approval.status in {ApprovalStatus.DENIED, ApprovalStatus.EXPIRED}:
            return _tool_error_result(
                call,
                approval.status.value,
                f"Tool execution was {approval.status.value} by approval decision.",
            )
        if approval.status is not ApprovalStatus.APPROVED:
            raise ApprovalInterrupt(approval.id)
        current_fingerprint = await _workspace_fingerprint(
            Path(approval.workspace_path)
        )
        if (
            approval.arguments_hash != expected_arguments_hash
            or approval.tool_version != expected_tool_version
            or approval.workspace_fingerprint != expected_workspace_fingerprint
            or current_fingerprint != approval.workspace_fingerprint
            or approval.capabilities != expected_capabilities
        ):
            raise CorruptRuntimeStateError("Approval binding changed before resume.")
        return await self._execute_tool_call(call, approval_granted=True)

    async def _execute_tool_call(
        self,
        call: ToolCall,
        *,
        approval_granted: bool = False,
    ) -> ToolResultMessage:
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
            approval_granted=approval_granted,
        )

    async def _resume_command(self, snapshot: Any) -> Any:
        interrupts = getattr(snapshot, "interrupts", ())
        if not interrupts:
            return None
        value = getattr(interrupts[0], "value", None)
        approval_id = _approval_id_from_interrupt_value(value)
        approval = await self._require_approval_repository().get(approval_id)
        if approval.status is ApprovalStatus.PENDING:
            raise ApprovalInterrupt(approval.id)
        return Command(
            resume={
                "approval_id": str(approval.id),
                "status": approval.status.value,
            }
        )

    def _raise_if_interrupted(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        interrupts = result.get("__interrupt__")
        if not isinstance(interrupts, list) or not interrupts:
            return
        value = getattr(interrupts[0], "value", None)
        raise ApprovalInterrupt(_approval_id_from_interrupt_value(value))

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

    async def _validate(self, state: ModifyingAgentState) -> ModifyingAgentState:
        run = self._require_run()
        agent = self._require_agent()
        workspace = cast(Path, run.workspace_path)
        plan = self.validation_plan_resolver(workspace)
        if plan is None or not plan.gates:
            raise ModifyingAgentLoopFailed("no_validation_gates")
        report = await self.validation_runner(plan, run, agent)
        reports = [*state["validation_reports"], _validation_report_snapshot(report)]
        if report.report.status == "passed":
            await self._emit(
                EventType.VERIFICATION_CREATED,
                {
                    "status": "passed",
                    "attempt": len(reports),
                    "summary": report.report.summary,
                },
                f"validation:{len(reports)}",
            )
            return {
                **state,
                "validation_reports": reports,
                "phase": "validation_passed",
            }
        if not _validation_failure_is_reworkable(report):
            raise ModifyingAgentLoopFailed(report.report.summary)
        if state["validation_rework_count"] >= plan.max_rework_attempts:
            raise ModifyingAgentLoopFailed("validation rework attempts exhausted")
        await self._emit(
            EventType.VERIFICATION_CREATED,
            {
                "status": "failed",
                "attempt": len(reports),
                "summary": report.report.summary,
                "reworkable": True,
            },
            f"validation:{len(reports)}",
        )
        return {
            **state,
            "validation_reports": reports,
            "phase": "validation_failed_reworkable",
        }

    async def _validation_feedback(
        self,
        state: ModifyingAgentState,
    ) -> ModifyingAgentState:
        turn = ModelTurn.model_validate(state["last_turn"])
        latest = state["validation_reports"][-1]
        return {
            **state,
            "messages": [
                *state["messages"],
                turn.assistant.model_dump(mode="json"),
                SystemMessage(
                    content=(
                        "Validation failed. Rework the implementation using this "
                        f"bounded evidence, then call repo.diff again: {latest}"
                    )
                ).model_dump(mode="json"),
            ],
            "validation_rework_count": state["validation_rework_count"] + 1,
            "stagnant_turns": 0,
            "phase": "validation_feedback",
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
                "validation_complete": True,
            },
            "final-answer",
        )
        return {
            **state,
            "phase": "completed",
            "final_answer": answer[:32768],
            "result_summary": (
                f"Modifying repository task produced validated changes after "
                f"{state['model_turn_count']} model turn(s), "
                f"{state['tool_call_count']} tool call(s), and "
                f"{state['successful_writes']} write(s)."
            ),
        }

    async def _prepare_context(
        self,
        *,
        run: Run,
        agent: Agent,
        messages: list[ModelMessage],
        rolling_summary: str,
    ) -> PreparedContext | None:
        if self.context_manager is None or self.budget_policy is None:
            return None
        return await self.context_manager.prepare_request(
            run_id=run.id,
            agent_id=agent.id,
            graph_name=MODIFYING_CODING_GRAPH,
            graph_version=MODIFYING_CODING_VERSION,
            messages=messages,
            rolling_summary=rolling_summary,
            policy=ContextPolicy(
                soft_context_tokens=self.budget_policy.soft_context_tokens,
                hard_context_tokens=self.budget_policy.hard_context_tokens,
                recent_context_tokens=self.budget_policy.recent_context_tokens,
            ),
        )

    async def _record_context_compaction(
        self,
        run: Run,
        agent: Agent,
        prepared: PreparedContext,
    ) -> None:
        if self.budget_repository is None:
            return
        await self.budget_repository.record_compaction(
            ContextCompactionRecord(
                run_id=run.id,
                agent_id=agent.id,
                graph_name=MODIFYING_CODING_GRAPH,
                graph_version=MODIFYING_CODING_VERSION,
                before_estimated_tokens=prepared.before_estimated_tokens,
                after_estimated_tokens=prepared.after_estimated_tokens,
                summary=prepared.rolling_summary,
                artifact_refs=_uuid_artifact_refs(prepared.artifact_refs),
            )
        )

    async def _load_budget_ledger(
        self,
        run_id: UUID,
        state: ModifyingAgentState,
    ) -> BudgetLedger:
        if self.budget_repository is not None:
            record = await self.budget_repository.get_ledger(run_id)
            return _ledger_from_record(record)
        return _ledger_from_state(state.get("budget_ledger", {}))

    async def _persist_budget_ledger(
        self,
        run_id: UUID,
        ledger: BudgetLedger,
    ) -> None:
        if self.budget_repository is not None:
            await self.budget_repository.upsert_ledger(
                _record_from_ledger(run_id, ledger)
            )

    async def _evaluate_budget_before_model_call(
        self,
        *,
        run_id: UUID,
        ledger: BudgetLedger,
        request_messages: list[ModelMessage],
        before_estimated_tokens: int,
        turn: int,
    ) -> BudgetLedger:
        if self.budget_policy is None:
            return ledger
        now = datetime.now(UTC)
        before_decision = evaluate_budget(
            ledger,
            self.budget_policy,
            estimated_prompt_tokens=before_estimated_tokens,
            now=now,
        )
        estimated_prompt_tokens = estimate_messages_tokens(request_messages)
        decision = evaluate_budget(
            ledger,
            self.budget_policy,
            estimated_prompt_tokens=estimated_prompt_tokens,
            now=now,
        )
        threshold_status = decision.value
        if decision is BudgetDecision.WITHIN_BUDGET and before_decision in {
            BudgetDecision.COMPACT,
            BudgetDecision.FINAL_ANSWER,
        }:
            threshold_status = before_decision.value
            await self._emit(
                EventType.BUDGET_THRESHOLD_REACHED,
                {
                    "turn": turn,
                    "decision": before_decision.value,
                    "before_estimated_tokens": before_estimated_tokens,
                    "estimated_prompt_tokens": estimated_prompt_tokens,
                },
                f"budget-threshold:{turn}",
            )
        elif decision in {BudgetDecision.COMPACT, BudgetDecision.FINAL_ANSWER}:
            await self._emit(
                EventType.BUDGET_THRESHOLD_REACHED,
                {
                    "turn": turn,
                    "decision": decision.value,
                    "before_estimated_tokens": before_estimated_tokens,
                    "estimated_prompt_tokens": estimated_prompt_tokens,
                },
                f"budget-threshold:{turn}",
            )
        updated = replace(ledger, threshold_status=threshold_status)
        await self._persist_budget_ledger(run_id, updated)
        if decision is BudgetDecision.EXHAUSTED:
            exhausted = replace(
                updated,
                threshold_status=BudgetDecision.EXHAUSTED.value,
            )
            await self._persist_budget_ledger(run_id, exhausted)
            await self._emit(
                EventType.BUDGET_EXHAUSTED,
                {
                    "turn": turn,
                    "estimated_prompt_tokens": estimated_prompt_tokens,
                    "total_tokens": exhausted.total_tokens,
                    "reasoning_tokens": exhausted.total_reasoning_tokens,
                    "active_seconds": exhausted.active_seconds_at(now),
                },
                f"budget-exhausted:{turn}",
            )
            raise ModifyingAgentLoopFailed(
                "budget_exhausted: token or wall-clock budget exhausted"
            )
        return updated

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
        "validation_rework_count": 0,
        "validation_reports": [],
        "phase": "created",
        "force_final": False,
        "rolling_summary": "",
        "budget_ledger": {},
        "context_artifact_refs": [],
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
        "validation_rework_count",
        "validation_reports",
        "phase",
    }
    if not required.issubset(value):
        raise CorruptRuntimeStateError("Modifying graph state is incomplete.")
    return cast(ModifyingAgentState, value)


def _ledger_from_state(payload: dict[str, Any]) -> BudgetLedger:
    return BudgetLedger(
        total_input_tokens=int(payload.get("total_input_tokens", 0)),
        total_output_tokens=int(payload.get("total_output_tokens", 0)),
        total_reasoning_tokens=int(payload.get("total_reasoning_tokens", 0)),
        active_seconds=int(payload.get("active_seconds", 0)),
        model_call_count=int(payload.get("model_call_count", 0)),
        threshold_status=str(
            payload.get("threshold_status", BudgetDecision.WITHIN_BUDGET.value)
        ),
    )


def _ledger_from_record(record: RunBudgetLedgerRecord) -> BudgetLedger:
    return BudgetLedger(
        total_input_tokens=record.total_input_tokens,
        total_output_tokens=record.total_output_tokens,
        total_reasoning_tokens=record.total_reasoning_tokens,
        active_seconds=record.active_seconds,
        model_call_count=record.model_call_count,
        threshold_status=record.threshold_status,
        active_window_started_at=record.active_window_started_at,
    )


def _record_from_ledger(
    run_id: UUID,
    ledger: BudgetLedger,
) -> RunBudgetLedgerRecord:
    return RunBudgetLedgerRecord(
        run_id=run_id,
        total_input_tokens=ledger.total_input_tokens,
        total_output_tokens=ledger.total_output_tokens,
        total_reasoning_tokens=ledger.total_reasoning_tokens,
        active_seconds=ledger.active_seconds,
        model_call_count=ledger.model_call_count,
        threshold_status=ledger.threshold_status,
        active_window_started_at=ledger.active_window_started_at,
    )


def _ledger_to_state(ledger: BudgetLedger) -> dict[str, Any]:
    return {
        "total_input_tokens": ledger.total_input_tokens,
        "total_output_tokens": ledger.total_output_tokens,
        "total_reasoning_tokens": ledger.total_reasoning_tokens,
        "active_seconds": ledger.active_seconds,
        "model_call_count": ledger.model_call_count,
        "threshold_status": ledger.threshold_status,
        "active_window_started_at": (
            ledger.active_window_started_at.isoformat()
            if ledger.active_window_started_at is not None
            else None
        ),
    }


def _uuid_artifact_refs(artifact_refs: list[str]) -> list[UUID]:
    refs: list[UUID] = []
    for artifact_ref in artifact_refs:
        try:
            refs.append(UUID(artifact_ref))
        except ValueError:
            continue
    return refs


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


def _approval_interrupt_payload(approval: DurableApproval) -> dict[str, object]:
    return {
        "approval_id": str(approval.id),
        "tool_call_id": approval.tool_call_id,
        "tool": approval.tool_name,
        "args_summary": _arguments_summary(approval.canonical_arguments),
        "risk": approval.risk_level,
        "expires_at": approval.expires_at.isoformat(),
    }


def _arguments_summary(arguments: dict[str, object]) -> str:
    if "argv" in arguments and isinstance(arguments["argv"], list):
        argv = " ".join(str(item) for item in arguments["argv"])
        return argv[:500]
    return json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )[:500]


def _approval_id_from_interrupt_value(value: object) -> UUID:
    if not isinstance(value, dict):
        raise CorruptRuntimeStateError("Approval interrupt payload is invalid.")
    approval_id = value.get("approval_id")
    if not isinstance(approval_id, str):
        raise CorruptRuntimeStateError("Approval interrupt is missing approval_id.")
    return UUID(approval_id)


def _resume_approval_id(value: object) -> UUID:
    if not isinstance(value, dict):
        raise CorruptRuntimeStateError("Approval resume payload is invalid.")
    approval_id = value.get("approval_id")
    if not isinstance(approval_id, str):
        raise CorruptRuntimeStateError("Approval resume is missing approval_id.")
    return UUID(approval_id)


def _tool_error_result(
    call: ToolCall,
    status: str,
    message: str,
) -> ToolResultMessage:
    return ToolResultMessage(
        call_id=call.call_id,
        content=json.dumps(
            {
                "status": status,
                "error": message,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        is_error=True,
    )


async def _workspace_fingerprint(workspace: Path) -> str:
    resolved = workspace.resolve()
    parts = [str(resolved)]
    for args in (
        ("rev-parse", "HEAD"),
        ("diff", "--binary"),
        ("diff", "--cached", "--binary"),
    ):
        parts.append(await _git_output(resolved, args))
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


async def _git_output(workspace: Path, args: tuple[str, ...]) -> str:
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as error:
        return f"git-unavailable:{type(error).__name__}"
    if completed.returncode != 0:
        return f"git-error:{' '.join(args)}:{completed.stderr[:500]}"
    return completed.stdout


def _copy_invocation(
    invocation: DurableToolInvocation,
    **updates: object,
) -> DurableToolInvocation:
    return replace(invocation, **updates)  # type: ignore[arg-type]


def _resolve_validation_plan(workspace: Path) -> ValidationPlan | None:
    return load_validation_config(workspace) or detect_validation_plan(workspace)


def _validation_report_snapshot(report: ValidationReportWithGates) -> dict[str, Any]:
    return {
        "id": str(report.report.id),
        "status": report.report.status,
        "summary": report.report.summary,
        "gates": [
            {
                **asdict(gate),
                "id": str(gate.id),
                "report_id": str(gate.report_id),
                "run_id": str(gate.run_id),
                "created_at": gate.created_at.isoformat(),
            }
            for gate in report.gates
        ],
    }


def _validation_failure_is_reworkable(report: ValidationReportWithGates) -> bool:
    blocking = [
        gate for gate in report.gates if gate.required and gate.status != "passed"
    ]
    return bool(blocking) and all(
        gate.failure_kind == "command_failed" for gate in blocking
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))
