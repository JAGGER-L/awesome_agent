from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any, cast
from uuid import UUID

from langgraph.types import Command, interrupt

from awesome_agent.artifacts.repository import ArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import ApprovalStatus, EventType
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    ModelMessage,
    SystemMessage,
    ToolCall,
    ToolResultMessage,
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
from awesome_agent.runtime.budget import (
    BudgetDecision,
    BudgetLedger,
    BudgetPolicy,
    estimate_messages_tokens,
    evaluate_budget,
)
from awesome_agent.runtime.context import ContextManager, ContextPolicy, PreparedContext
from awesome_agent.runtime.dispatch import (
    ApprovalInterrupt,
    CorruptRuntimeStateError,
    PermanentExecutionError,
)
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.models import ApprovalRequired, ToolDenied
from awesome_agent.tools.registry import ToolRegistry
from awesome_agent.tools.repository import (
    RepositoryRecoveryRequired,
    canonical_arguments_hash_from_arguments,
    execute_repository_call,
    parse_tool_call_arguments,
    repository_tool_effect_metadata,
    tool_invocation_uuid,
)

_TOOL_RESULT_OFFLOAD_CHARS = 12_000
_TOOL_RESULT_HEAD_CHARS = 8_000
_TOOL_RESULT_TAIL_CHARS = 3_000


class ModifyingContextMiddleware:
    def __init__(
        self,
        *,
        context_manager: ContextManager | None,
        budget_repository: BudgetRepository | None,
        budget_policy: BudgetPolicy | None,
        runtime_route: str,
    ) -> None:
        self.context_manager = context_manager
        self.budget_repository = budget_repository
        self.budget_policy = budget_policy
        self.runtime_route = runtime_route

    async def prepare_context(
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
            runtime_route=self.runtime_route,
            messages=messages,
            rolling_summary=rolling_summary,
            policy=ContextPolicy(
                soft_context_tokens=self.budget_policy.soft_context_tokens,
                hard_context_tokens=self.budget_policy.hard_context_tokens,
                recent_context_tokens=self.budget_policy.recent_context_tokens,
            ),
        )

    async def record_compaction(
        self,
        *,
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
                runtime_route=self.runtime_route,
                before_estimated_tokens=prepared.before_estimated_tokens,
                after_estimated_tokens=prepared.after_estimated_tokens,
                summary=prepared.rolling_summary,
                artifact_refs=_uuid_artifact_refs(prepared.artifact_refs),
            )
        )


class ModifyingBudgetExhausted(PermanentExecutionError):
    pass


class ModifyingBudgetMiddleware:
    def __init__(
        self,
        *,
        budget_repository: BudgetRepository | None,
        budget_policy: BudgetPolicy | None,
        emit: Any,
    ) -> None:
        self.budget_repository = budget_repository
        self.budget_policy = budget_policy
        self.emit = emit

    async def load_ledger(
        self,
        run_id: UUID,
        state_payload: dict[str, Any],
    ) -> BudgetLedger:
        if self.budget_repository is not None:
            record = await self.budget_repository.get_ledger(run_id)
            return _ledger_from_record(record)
        return _ledger_from_state(state_payload)

    async def persist_ledger(
        self,
        run_id: UUID,
        ledger: BudgetLedger,
    ) -> None:
        if self.budget_repository is not None:
            await self.budget_repository.upsert_ledger(
                _record_from_ledger(run_id, ledger)
            )

    async def evaluate_before_model_call(
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
            await self.emit(
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
            await self.emit(
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
        await self.persist_ledger(run_id, updated)
        if decision is BudgetDecision.EXHAUSTED:
            exhausted = replace(
                updated,
                threshold_status=BudgetDecision.EXHAUSTED.value,
            )
            await self.persist_ledger(run_id, exhausted)
            await self.emit(
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
            raise ModifyingBudgetExhausted(
                "budget_exhausted: token or wall-clock budget exhausted"
            )
        return updated


class ModifyingArtifactMiddleware:
    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore | None,
        artifact_repository: ArtifactMetadataRepository | None,
    ) -> None:
        self.artifact_store = artifact_store
        self.artifact_repository = artifact_repository

    async def offload_result_if_needed(
        self,
        *,
        call_id: str,
        result: ToolResultMessage,
        run: Run,
        agent: Agent,
    ) -> ToolResultMessage:
        if (
            self.artifact_store is None
            or self.artifact_repository is None
            or len(result.content) <= _TOOL_RESULT_OFFLOAD_CHARS
        ):
            return result
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


class ModifyingApprovalMiddleware:
    def __init__(
        self,
        *,
        approval_repository: ApprovalRepository | None,
        tool_repository: ToolInvocationRepository | None,
        approval_default_expiry: timedelta,
        emit: Any,
    ) -> None:
        self.approval_repository = approval_repository
        self.tool_repository = tool_repository
        self.approval_default_expiry = approval_default_expiry
        self.emit = emit

    async def interrupt_for_approval(
        self,
        *,
        call: ToolCall,
        invocation: DurableToolInvocation,
        arguments: dict[str, Any],
        arguments_hash: str,
        workspace: Path,
        tool_version: str,
        risk_level: str,
        run: Run,
        agent: Agent,
        execute_tool_call: Any,
    ) -> ToolResultMessage:
        if self.approval_repository is None:
            raise CorruptRuntimeStateError("Approval repository is not configured.")
        approval = await self.upsert_approval(
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
        if self.tool_repository is not None:
            await self.tool_repository.upsert(
                _copy_invocation(
                    invocation,
                    status="approval_pending",
                    updated_at=datetime.now(UTC),
                )
            )
        payload = approval_interrupt_payload(approval)
        await self.emit(
            EventType.APPROVAL_REQUESTED,
            {
                **payload,
                "reason": "waiting_approval",
                "agent_id": str(agent.id),
            },
            f"approval:{approval.tool_call_id}",
        )
        resume = interrupt(payload)
        return await self.resume_approved_tool_call(
            call=call,
            approval_id=resume_approval_id(resume),
            expected_arguments_hash=arguments_hash,
            expected_tool_version=tool_version,
            expected_workspace_fingerprint=approval.workspace_fingerprint,
            expected_capabilities=approval.capabilities,
            execute_tool_call=execute_tool_call,
        )

    async def upsert_approval(
        self,
        *,
        call: ToolCall,
        invocation: DurableToolInvocation,
        arguments: dict[str, Any],
        arguments_hash: str,
        workspace: Path,
        tool_version: str,
        risk_level: str,
        run: Run,
        agent: Agent,
    ) -> DurableApproval:
        repository = self._require_approval_repository()
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
            workspace_fingerprint=await workspace_fingerprint(workspace),
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

    async def resume_approved_tool_call(
        self,
        *,
        call: ToolCall,
        approval_id: UUID,
        expected_arguments_hash: str,
        expected_tool_version: str,
        expected_workspace_fingerprint: str,
        expected_capabilities: list[str],
        execute_tool_call: Any,
    ) -> ToolResultMessage:
        approval = await self._require_approval_repository().get(approval_id)
        if approval.tool_call_id != call.call_id:
            raise CorruptRuntimeStateError("Approval resume tool call mismatch.")
        if approval.status in {ApprovalStatus.DENIED, ApprovalStatus.EXPIRED}:
            return tool_error_result(
                call,
                approval.status.value,
                f"Tool execution was {approval.status.value} by approval decision.",
            )
        if approval.status is not ApprovalStatus.APPROVED:
            raise ApprovalInterrupt(approval.id)
        current_fingerprint = await workspace_fingerprint(Path(approval.workspace_path))
        if (
            approval.arguments_hash != expected_arguments_hash
            or approval.tool_version != expected_tool_version
            or approval.workspace_fingerprint != expected_workspace_fingerprint
            or current_fingerprint != approval.workspace_fingerprint
            or approval.capabilities != expected_capabilities
        ):
            raise CorruptRuntimeStateError("Approval binding changed before resume.")
        return cast(
            ToolResultMessage,
            await execute_tool_call(call, approval_granted=True),
        )

    async def resume_command(self, snapshot: Any) -> Any:
        interrupts = getattr(snapshot, "interrupts", ())
        if not interrupts:
            return None
        value = getattr(interrupts[0], "value", None)
        approval_id = approval_id_from_interrupt_value(value)
        approval = await self._require_approval_repository().get(approval_id)
        if approval.status is ApprovalStatus.PENDING:
            raise ApprovalInterrupt(approval.id)
        return Command(
            resume={
                "approval_id": str(approval.id),
                "status": approval.status.value,
            }
        )

    def raise_if_interrupted(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        interrupts = result.get("__interrupt__")
        if not isinstance(interrupts, list) or not interrupts:
            return
        value = getattr(interrupts[0], "value", None)
        raise ApprovalInterrupt(approval_id_from_interrupt_value(value))

    def _require_approval_repository(self) -> ApprovalRepository:
        if self.approval_repository is None:
            raise CorruptRuntimeStateError("Approval repository is not configured.")
        return self.approval_repository


class ModifyingToolMiddleware:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        executor: ToolExecutor,
        tool_repository: ToolInvocationRepository | None,
        approval_middleware: ModifyingApprovalMiddleware,
        artifact_middleware: ModifyingArtifactMiddleware,
        max_tool_calls: int,
        no_progress_turns: int,
        fault_hook: Any,
        emit: Any,
    ) -> None:
        self.registry = registry
        self.executor = executor
        self.tool_repository = tool_repository
        self.approval_middleware = approval_middleware
        self.artifact_middleware = artifact_middleware
        self.max_tool_calls = max_tool_calls
        self.no_progress_turns = no_progress_turns
        self.fault_hook = fault_hook
        self.emit = emit

    async def execute_turn(
        self,
        *,
        state: dict[str, Any],
        turn_assistant: Any,
        calls: list[ToolCall],
        run: Run,
        agent: Agent,
    ) -> dict[str, Any]:
        if not calls:
            return state
        if state["tool_call_count"] + len(calls) > self.max_tool_calls:
            return {
                **state,
                "messages": [
                    *state["messages"],
                    turn_assistant.model_dump(mode="json"),
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
                result = await self.execute_durable_tool_call(
                    call,
                    run=run,
                    agent=agent,
                )
            except RepositoryRecoveryRequired as error:
                raise CorruptRuntimeStateError(str(error)) from error
            except ToolDenied as error:
                result = tool_error_result(call, "denied", str(error))
            latency_ms = elapsed_ms(started)
            result = await self.artifact_middleware.offload_result_if_needed(
                call_id=call.call_id,
                result=result,
                run=run,
                agent=agent,
            )
            if call.name == "repo.apply_patch" and not result.is_error:
                successful_writes += 1
                final_diff_after_write = False
            if call.name == "repo.diff" and not result.is_error and successful_writes:
                final_diff_after_write = True
            fingerprint = hashlib.sha256(
                f"{call.name}\0{call.arguments_json}\0{result.content}".encode()
            ).hexdigest()
            fingerprints.append(fingerprint)
            await self.emit(
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
            turn_assistant.model_dump(mode="json"),
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
        updated = {
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

    async def execute_durable_tool_call(
        self,
        call: ToolCall,
        *,
        run: Run,
        agent: Agent,
    ) -> ToolResultMessage:
        if self.tool_repository is None:
            return await self.execute_tool_call(call, run=run, agent=agent)
        workspace = cast(Any, run.workspace_path)
        arguments = parse_tool_call_arguments(call)
        spec, _ = self.registry.resolve(call.name)
        arguments_fingerprint = canonical_arguments_hash_from_arguments(arguments)
        idempotency_key = idempotency_key_for_tool_invocation(
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
            result = await self.execute_tool_call(call, run=run, agent=agent)
        except ToolDenied as error:
            result = tool_error_result(call, "denied", str(error))
        except ApprovalRequired:
            result = await self.approval_middleware.interrupt_for_approval(
                call=call,
                invocation=invocation,
                arguments=arguments,
                arguments_hash=arguments_fingerprint,
                workspace=workspace,
                tool_version=spec.version,
                risk_level=spec.risk_level.value,
                run=run,
                agent=agent,
                execute_tool_call=lambda resumed_call, *, approval_granted=False: (
                    self.execute_tool_call(
                        resumed_call,
                        run=run,
                        agent=agent,
                        approval_granted=approval_granted,
                    )
                ),
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
        expected_postimage_hashes = extract_postimage_hashes(result.content)
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

    async def execute_tool_call(
        self,
        call: ToolCall,
        *,
        run: Run,
        agent: Agent,
        approval_granted: bool = False,
    ) -> ToolResultMessage:
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


def modifying_ledger_to_state(ledger: BudgetLedger) -> dict[str, Any]:
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


def _uuid_artifact_refs(artifact_refs: list[str]) -> list[UUID]:
    refs: list[UUID] = []
    for artifact_ref in artifact_refs:
        try:
            refs.append(UUID(artifact_ref))
        except ValueError:
            continue
    return refs


def idempotency_key_for_tool_invocation(
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


def extract_postimage_hashes(content: str) -> dict[str, str]:
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


def approval_interrupt_payload(approval: DurableApproval) -> dict[str, object]:
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


def approval_id_from_interrupt_value(value: object) -> UUID:
    if not isinstance(value, dict):
        raise CorruptRuntimeStateError("Approval interrupt payload is invalid.")
    approval_id = value.get("approval_id")
    if not isinstance(approval_id, str):
        raise CorruptRuntimeStateError("Approval interrupt is missing approval_id.")
    return UUID(approval_id)


def resume_approval_id(value: object) -> UUID:
    if not isinstance(value, dict):
        raise CorruptRuntimeStateError("Approval resume payload is invalid.")
    approval_id = value.get("approval_id")
    if not isinstance(approval_id, str):
        raise CorruptRuntimeStateError("Approval resume is missing approval_id.")
    return UUID(approval_id)


def tool_error_result(
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


async def workspace_fingerprint(workspace: Path) -> str:
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


def elapsed_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))
