from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from enum import StrEnum
from math import isfinite
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

from awesome_agent.agent import (
    AgentRuntimeContext,
    AgentState,
    new_agent_state,
)
from awesome_agent.application.context import (
    frozen_context_manifests_share_lineage,
    frozen_context_snapshot_is_valid,
)
from awesome_agent.application.contracts import OperationAccepted
from awesome_agent.application.events import ApplicationEventProjector
from awesome_agent.application.operations import (
    OperationContinuation,
    OperationController,
)
from awesome_agent.application.turn_facts import (
    ObservedTurnFacts,
    observed_turn_facts,
)
from awesome_agent.config import TurnConfig
from awesome_agent.context import ExplicitPathError
from awesome_agent.conversation import (
    ConversationConflict,
    ConversationService,
    Thread,
    ThreadEntryKind,
    ThreadView,
    Turn,
    TurnStatus,
)
from awesome_agent.core.cancellation import (
    finish_bounded_cancellation_cleanup,
    finish_cancellation_safe,
)
from awesome_agent.core.events import EventEmitter
from awesome_agent.core.tools import ToolErrorCode, ToolResult, ToolStatus
from awesome_agent.core.tools.registry import ToolReplaySafety
from awesome_agent.modeling import ModelUsage
from awesome_agent.storage.checkpoints import CheckpointCorrupt, TurnCheckpointStore

logger = logging.getLogger(__name__)

_CANCELLATION_FACTS_TIMEOUT_SECONDS = 1.0
_CANCELLATION_EVENT_TIMEOUT_SECONDS = 10.0
_RESUMABLE_BUDGET_REASONS = frozenset(
    {
        "model_budget_exhausted",
        "tool_budget_exhausted",
        "active_time_budget_exhausted",
    }
)


class AgentGraph(Protocol):
    async def ainvoke(
        self,
        state: AgentState | None,
        config: dict[str, Any],
        *,
        context: AgentRuntimeContext,
    ) -> AgentState: ...


class ContextSnapshotValidator(Protocol):
    def __call__(
        self,
        state: AgentState,
        *,
        turn: Turn,
        view: ThreadView,
    ) -> bool: ...


type TurnConfigResolver = Callable[[Thread], TurnConfig]
type RuntimeContextFactory = Callable[
    [Turn, str, ApplicationEventProjector],
    Awaitable[AgentRuntimeContext],
]
type TurnExtensionPreparer = Callable[[], Awaitable[None]]
type ResumeClaim = Callable[[Turn], Awaitable[None]]
type ResumeFinished = Callable[[], Awaitable[None]]
type ToolReplaySafetyResolver = Callable[[str], ToolReplaySafety]


class TurnExecutionFailed(RuntimeError):
    pass


class TurnInputInvalid(TurnExecutionFailed):
    pass


class RecoveryStatus(StrEnum):
    FINALIZED = "finalized"
    RESUMABLE = "resumable"
    FAILED = "failed"
    CLEANED = "cleaned"
    INTERACTION_REQUIRED = "interaction_required"


class RecoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_id: str
    turn_id: str
    status: RecoveryStatus
    error_code: str | None = None


async def disabled_turn_extension_preparer() -> None:
    return None


async def disabled_change_reconciler() -> None:
    return None


def fail_closed_replay_safety(tool_name: str) -> ToolReplaySafety:
    del tool_name
    return ToolReplaySafety.NON_REPLAYABLE


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
        seal_changes: Callable[[str], Awaitable[None]],
        reconcile_changes: Callable[[], Awaitable[None]] = (disabled_change_reconciler),
        turn_input_preparer: Callable[[Turn, str], None] = lambda turn, content: None,
        turn_extension_preparer: TurnExtensionPreparer = (
            disabled_turn_extension_preparer
        ),
        context_snapshot_validator: ContextSnapshotValidator = (
            frozen_context_snapshot_is_valid
        ),
        tool_replay_safety: ToolReplaySafetyResolver = fail_closed_replay_safety,
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
        self._reconcile_changes = reconcile_changes
        self._turn_input_preparer = turn_input_preparer
        self._turn_extension_preparer = turn_extension_preparer
        self._context_snapshot_validator = context_snapshot_validator
        self._tool_replay_safety = tool_replay_safety
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def active_operation_id(self) -> str | None:
        return self._operations.active_operation_id

    async def submit_turn(
        self,
        thread_id: str,
        content: str,
        *,
        client_message_id: str,
    ) -> OperationAccepted:
        reservation = self._operations.reserve()
        begin_task: asyncio.Task[Turn] | None = None
        try:
            thread = (await self._conversation.read_thread(thread_id)).thread
            self._require_thread_workspace(thread)
            config = self._config_resolver(thread)
            begin_task = asyncio.create_task(
                self._conversation.begin_turn(
                    thread_id,
                    content,
                    config,
                    client_message_id=client_message_id,
                ),
                name=f"turn-begin:{client_message_id}",
            )
            turn = await asyncio.shield(begin_task)
        except asyncio.CancelledError as cancellation:
            if begin_task is not None:
                compensation = asyncio.create_task(
                    self._cancel_begun_turn(begin_task),
                    name=f"turn-begin-compensation:{client_message_id}",
                )
                try:
                    await _await_task_uninterruptibly(compensation)
                except BaseException:
                    logger.exception("Turn begin cancellation compensation failed.")
            self._operations.abort(reservation)
            raise cancellation
        except BaseException:
            self._operations.abort(reservation)
            raise

        async def execute(operation_id: str) -> None:
            projector = ApplicationEventProjector(
                emitter=self._emitter,
                thread_id=turn.thread_id,
                turn_id=turn.id,
                operation_id=operation_id,
                client_message_id=client_message_id,
            )
            await self._start_turn(turn, operation_id, projector)
            try:
                self._turn_input_preparer(turn, content)
            except asyncio.CancelledError:
                await self._finish_cancelled_turn(turn, projector)
                raise
            except ExplicitPathError as error:
                await self._fail_turn_preparation(
                    turn,
                    operation_id,
                    projector,
                    "invalid_explicit_path",
                )
                raise TurnInputInvalid(str(error)) from error
            except BaseException:
                await self._fail_turn_preparation(
                    turn,
                    operation_id,
                    projector,
                    "turn_preparation_failed",
                )
                raise
            await self._execute_turn(turn, operation_id, projector)

        try:
            handle = await self._operations.start_reserved(
                reservation,
                execute,
                thread_id=turn.thread_id,
                turn_id=turn.id,
                client_message_id=client_message_id,
            )
        except BaseException as error:
            self._operations.abort(reservation)
            compensation = asyncio.create_task(
                self._terminalize_unstarted_turn(
                    turn,
                    cancelled=isinstance(error, asyncio.CancelledError),
                ),
                name=f"turn-start-compensation:{turn.id}",
            )
            try:
                await _await_task_uninterruptibly(compensation)
            except BaseException:
                logger.exception(
                    "Turn operation-start compensation failed.",
                    extra={"turn_id": turn.id},
                )
            raise
        self._tasks[handle.operation_id] = handle.task
        handle.task.add_done_callback(self._task_completed)
        return OperationAccepted(
            operation_id=handle.operation_id,
            thread_id=thread_id,
            turn_id=turn.id,
            client_message_id=client_message_id,
        )

    async def _cancel_begun_turn(self, begin_task: asyncio.Task[Turn]) -> None:
        begun = await _await_task_uninterruptibly(begin_task)
        await self._conversation.cancel_turn(begun.id)

    async def _terminalize_unstarted_turn(
        self,
        turn: Turn,
        *,
        cancelled: bool,
    ) -> None:
        current = next(
            (
                item
                for item in (await self._conversation.read_thread(turn.thread_id)).turns
                if item.id == turn.id
            ),
            None,
        )
        if current is None or current.status is not TurnStatus.IN_PROGRESS:
            return
        if cancelled:
            await self._conversation.cancel_turn(turn.id)
        else:
            await self._conversation.fail_turn(turn.id, "operation_start_failed")

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

    async def resume_unfinished(
        self,
        thread_id: str,
        *,
        expected_turn_id: str | None = None,
        continuation: OperationContinuation | None = None,
        claim: ResumeClaim | None = None,
        finished: ResumeFinished | None = None,
    ) -> OperationAccepted:
        reservation = self._operations.reserve(continuation=continuation)
        try:
            view = await self._conversation.read_thread(thread_id)
            self._require_thread_workspace(view.thread)
            turn = next(
                (
                    item
                    for item in view.turns
                    if item.status is TurnStatus.IN_PROGRESS
                    and (expected_turn_id is None or item.id == expected_turn_id)
                ),
                None,
            )
            if turn is None:
                raise TurnExecutionFailed("recovery_stale")
            try:
                state = await self._checkpoints.latest_state(turn.id)
            except CheckpointCorrupt as error:
                await self._fail_recovery(turn, "checkpoint_corrupt")
                raise TurnExecutionFailed("checkpoint_corrupt") from error
            if state is None:
                await self._fail_recovery(turn, "checkpoint_missing")
                raise TurnExecutionFailed("checkpoint_missing")
            if not _checkpoint_identity_is_valid(
                state,
                view=view,
                turn=turn,
                workspace_key=self._workspace_key,
            ):
                await self._fail_recovery(turn, "checkpoint_corrupt")
                raise TurnExecutionFailed("checkpoint_corrupt")
            try:
                reconciled_turn = await _reconcile_frozen_context_snapshot(
                    state,
                    view,
                    turn,
                    self._context_snapshot_validator,
                    self._conversation,
                )
            except ConversationConflict as error:
                await self._fail_recovery(turn, "context_snapshot_conflict")
                raise TurnExecutionFailed("context_snapshot_conflict") from error
            if reconciled_turn is None:
                await self._fail_recovery(turn, "context_snapshot_missing")
                raise TurnExecutionFailed("context_snapshot_missing")
            turn = reconciled_turn
            client_message_id = _client_message_id(view, turn)
            if claim is not None:
                await claim(turn)
        except BaseException:
            self._operations.abort(reservation)
            raise

        async def execute(operation_id: str) -> None:
            projector = ApplicationEventProjector(
                emitter=self._emitter,
                thread_id=turn.thread_id,
                turn_id=turn.id,
                operation_id=operation_id,
                client_message_id=client_message_id,
            )
            try:
                await self._start_turn(turn, operation_id, projector)
                await self._execute_turn(turn, operation_id, projector, resume=True)
            finally:
                if finished is not None:
                    try:
                        await finished()
                    except (Exception, asyncio.CancelledError):
                        logger.warning(
                            "Recovery continuation notification failed.",
                            exc_info=True,
                        )

        try:
            handle = await self._operations.start_reserved(
                reservation,
                execute,
                thread_id=turn.thread_id,
                turn_id=turn.id,
                client_message_id=client_message_id,
            )
        except BaseException:
            self._operations.abort(reservation)
            raise
        self._tasks[handle.operation_id] = handle.task
        handle.task.add_done_callback(self._task_completed)
        return OperationAccepted(
            operation_id=handle.operation_id,
            thread_id=thread_id,
            turn_id=turn.id,
            client_message_id=client_message_id,
        )

    async def abort_recovery(self, thread_id: str, turn_id: str) -> None:
        view = await self._conversation.read_thread(thread_id)
        self._require_thread_workspace(view.thread)
        turn = next((item for item in view.turns if item.id == turn_id), None)
        if turn is None or turn.status is not TurnStatus.IN_PROGRESS:
            raise TurnExecutionFailed("recovery_stale")
        facts = await self._latest_observed_facts(turn.id)
        await self._conversation.fail_turn(
            turn.id,
            "recovery_aborted",
            usage=facts.usage,
            context_manifest=facts.context_manifest,
        )
        await self._cleanup_turn(turn.id)

    async def reconcile_startup(self) -> tuple[RecoveryResult, ...]:
        await self._reconcile_changes()
        results: list[RecoveryResult] = []
        for thread in await self._conversation.list_threads(self._workspace_key):
            view = await self._conversation.read_thread(thread.id)
            for turn in view.turns:
                checkpoint_exists = await self._checkpoints.exists(turn.id)
                if turn.status is not TurnStatus.IN_PROGRESS:
                    if checkpoint_exists:
                        await self._checkpoints.delete(turn.id)
                        results.append(
                            RecoveryResult(
                                thread_id=thread.id,
                                turn_id=turn.id,
                                status=RecoveryStatus.CLEANED,
                            )
                        )
                    continue
                if not checkpoint_exists:
                    await self._fail_recovery(turn, "checkpoint_missing")
                    results.append(
                        RecoveryResult(
                            thread_id=thread.id,
                            turn_id=turn.id,
                            status=RecoveryStatus.FAILED,
                            error_code="checkpoint_missing",
                        )
                    )
                    continue
                try:
                    state = await self._checkpoints.latest_state(turn.id)
                except CheckpointCorrupt:
                    await self._fail_recovery(turn, "checkpoint_corrupt")
                    results.append(
                        RecoveryResult(
                            thread_id=thread.id,
                            turn_id=turn.id,
                            status=RecoveryStatus.FAILED,
                            error_code="checkpoint_corrupt",
                        )
                    )
                    continue
                if state is None:
                    await self._fail_recovery(turn, "checkpoint_missing")
                    results.append(
                        RecoveryResult(
                            thread_id=thread.id,
                            turn_id=turn.id,
                            status=RecoveryStatus.FAILED,
                            error_code="checkpoint_missing",
                        )
                    )
                    continue
                if not _checkpoint_identity_is_valid(
                    state,
                    view=view,
                    turn=turn,
                    workspace_key=self._workspace_key,
                ):
                    await self._fail_recovery(turn, "checkpoint_corrupt")
                    results.append(
                        RecoveryResult(
                            thread_id=thread.id,
                            turn_id=turn.id,
                            status=RecoveryStatus.FAILED,
                            error_code="checkpoint_corrupt",
                        )
                    )
                    continue
                try:
                    reconciled_turn = await _reconcile_frozen_context_snapshot(
                        state,
                        view,
                        turn,
                        self._context_snapshot_validator,
                        self._conversation,
                    )
                except ConversationConflict:
                    await self._fail_recovery(turn, "context_snapshot_conflict")
                    results.append(
                        RecoveryResult(
                            thread_id=thread.id,
                            turn_id=turn.id,
                            status=RecoveryStatus.FAILED,
                            error_code="context_snapshot_conflict",
                        )
                    )
                    continue
                if reconciled_turn is None:
                    await self._fail_recovery(turn, "context_snapshot_missing")
                    results.append(
                        RecoveryResult(
                            thread_id=thread.id,
                            turn_id=turn.id,
                            status=RecoveryStatus.FAILED,
                            error_code="context_snapshot_missing",
                        )
                    )
                    continue
                turn = reconciled_turn
                if state["final_answer"] is not None:
                    facts = observed_turn_facts(state)
                    await self._conversation.complete_turn(
                        turn.id,
                        state["final_answer"],
                        facts.usage,
                        state["termination_reason"] or "completed",
                        facts.context_manifest,
                    )
                    await self._cleanup_committed_turn(turn.id)
                    await self._emit_recovery_events(turn)
                    results.append(
                        RecoveryResult(
                            thread_id=thread.id,
                            turn_id=turn.id,
                            status=RecoveryStatus.FINALIZED,
                        )
                    )
                    continue
                resumable_budget_interruption = _resumable_budget_interruption(
                    state,
                    turn,
                )
                if (
                    state["termination_reason"] is not None
                    and not resumable_budget_interruption
                ):
                    reason = state["termination_reason"]
                    await self._fail_recovery(
                        turn,
                        reason,
                        facts=observed_turn_facts(state),
                    )
                    results.append(
                        RecoveryResult(
                            thread_id=thread.id,
                            turn_id=turn.id,
                            status=RecoveryStatus.FAILED,
                            error_code=reason,
                        )
                    )
                    continue
                if not resumable_budget_interruption and _uncertain_tool_call(
                    state,
                    self._tool_replay_safety,
                ):
                    results.append(
                        RecoveryResult(
                            thread_id=thread.id,
                            turn_id=turn.id,
                            status=RecoveryStatus.INTERACTION_REQUIRED,
                        )
                    )
                    continue
                results.append(
                    RecoveryResult(
                        thread_id=thread.id,
                        turn_id=turn.id,
                        status=RecoveryStatus.RESUMABLE,
                    )
                )
        return tuple(results)

    async def _start_turn(
        self,
        turn: Turn,
        operation_id: str,
        projector: ApplicationEventProjector,
    ) -> None:
        try:
            await projector.turn_started()
        except asyncio.CancelledError as cancellation:
            await self._finish_cancelled_turn(turn, projector)
            raise cancellation
        except Exception:
            await self._finish_failed_active_turn(
                turn,
                operation_id,
                projector,
                "turn_start_failed",
                seal=False,
            )
            raise

    async def _execute_turn(
        self,
        turn: Turn,
        operation_id: str,
        projector: ApplicationEventProjector,
        *,
        resume: bool = False,
    ) -> None:
        try:
            await self._turn_extension_preparer()
        except asyncio.CancelledError as cancellation:
            await self._finish_cancelled_turn(turn, projector)
            raise cancellation
        except Exception:
            await self._finish_failed_active_turn(
                turn,
                operation_id,
                projector,
                "agent_initialization_failed",
                seal=False,
            )
            raise
        try:
            state = (
                None
                if resume
                else new_agent_state(
                    thread_id=turn.thread_id,
                    turn_id=turn.id,
                    workspace_key=self._workspace_key,
                    provider=turn.provider,
                    model=turn.model,
                    thinking_enabled=turn.thinking_enabled,
                )
            )
        except asyncio.CancelledError as cancellation:
            await self._finish_cancelled_turn(turn, projector)
            raise cancellation
        except Exception:
            await self._finish_failed_active_turn(
                turn,
                operation_id,
                projector,
                "agent_initialization_failed",
                seal=False,
            )
            raise
        try:
            runtime = await self._runtime_context_factory(turn, operation_id, projector)
        except asyncio.CancelledError as cancellation:
            await self._finish_cancelled_turn(turn, projector)
            raise cancellation
        except Exception:
            await self._finish_failed_active_turn(
                turn,
                operation_id,
                projector,
                "agent_initialization_failed",
                seal=False,
            )
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
        except asyncio.CancelledError as cancellation:
            await self._finish_cancelled_turn(turn, projector)
            raise cancellation
        except Exception:
            await self._finish_failed_active_turn(
                turn,
                operation_id,
                projector,
                "agent_execution_failed",
                observe_facts=True,
            )
            raise

        try:
            reason = result["termination_reason"] or "completed"
            answer = result["final_answer"]
            facts = observed_turn_facts(result)
        except asyncio.CancelledError as cancellation:
            await self._finish_cancelled_turn(turn, projector)
            raise cancellation
        except Exception:
            await self._finish_failed_active_turn(
                turn,
                operation_id,
                projector,
                "agent_execution_failed",
                observe_facts=True,
            )
            raise
        if answer is None:
            await self._operations.commit_failed(
                operation_id,
                lambda: self._persist_failed_turn_and_cleanup(turn, reason, facts),
            )
            with suppress(Exception, asyncio.CancelledError):
                await self._operations.publish_committed(
                    operation_id,
                    lambda: projector.turn_failed(reason),
                )
            raise TurnExecutionFailed(reason)

        await self._operations.commit_completed(
            operation_id,
            lambda: self._persist_completed_turn_and_cleanup(
                turn, answer, reason, facts
            ),
        )
        try:
            await self._operations.publish_committed(
                operation_id,
                projector.turn_completed,
            )
        except Exception:
            logger.warning(
                "Turn terminal event delivery failed after completion.",
                exc_info=True,
            )

    async def _persist_completed_turn(
        self,
        turn: Turn,
        answer: str,
        reason: str,
        facts: ObservedTurnFacts,
    ) -> None:
        expected_manifest = await self._expected_terminal_manifest(turn, facts)
        await self._persist_terminal_write(
            lambda: self._conversation.complete_turn(
                turn.id,
                answer,
                facts.usage,
                reason,
                facts.context_manifest,
            ),
            committed=lambda: self._completed_turn_matches(
                turn,
                answer,
                reason,
                facts,
                expected_manifest,
            ),
            outcome="completed",
        )

    async def _persist_completed_turn_and_cleanup(
        self,
        turn: Turn,
        answer: str,
        reason: str,
        facts: ObservedTurnFacts,
    ) -> None:
        await self._persist_completed_turn(turn, answer, reason, facts)
        await self._cleanup_committed_turn(turn.id)

    async def _persist_failed_turn(
        self,
        turn: Turn,
        error_code: str,
        facts: ObservedTurnFacts,
    ) -> None:
        expected_manifest = await self._expected_terminal_manifest(turn, facts)
        await self._persist_terminal_write(
            lambda: self._conversation.fail_turn(
                turn.id,
                error_code,
                usage=facts.usage,
                context_manifest=facts.context_manifest,
            ),
            committed=lambda: self._failed_turn_matches(
                turn,
                error_code,
                facts,
                expected_manifest,
            ),
            outcome="failed",
        )

    async def _persist_failed_turn_and_cleanup(
        self,
        turn: Turn,
        error_code: str,
        facts: ObservedTurnFacts,
        *,
        seal: bool = True,
    ) -> None:
        await self._persist_failed_turn(turn, error_code, facts)
        await self._cleanup_committed_turn(turn.id, seal=seal)

    async def _persist_cancelled_turn(
        self,
        turn: Turn,
        facts: ObservedTurnFacts,
    ) -> None:
        expected_manifest = await self._expected_terminal_manifest(turn, facts)
        await self._persist_terminal_write(
            lambda: self._conversation.cancel_turn(
                turn.id,
                usage=facts.usage,
                context_manifest=facts.context_manifest,
            ),
            committed=lambda: self._cancelled_turn_matches(
                turn,
                facts,
                expected_manifest,
            ),
            outcome="cancelled",
        )

    @staticmethod
    async def _persist_terminal_write(
        action: Callable[[], Awaitable[object]],
        *,
        committed: Callable[[], Awaitable[bool]],
        outcome: str,
    ) -> None:
        try:
            await action()
        except (Exception, asyncio.CancelledError):
            if not await committed():
                raise
            logger.warning(
                "Turn %s write raised after its exact durable state committed.",
                outcome,
                exc_info=True,
            )

    async def _completed_turn_matches(
        self,
        turn: Turn,
        answer: str,
        reason: str,
        facts: ObservedTurnFacts,
        expected_manifest: tuple[dict[str, JsonValue], ...] | None,
    ) -> bool:
        if expected_manifest is None:
            return False
        current = await self._current_turn(turn)
        if current is None:
            return False
        view, observed = current
        assistant = next(
            (
                entry
                for entry in view.entries
                if entry.id == observed.assistant_entry_id
            ),
            None,
        )
        return (
            observed.status is TurnStatus.COMPLETED
            and assistant is not None
            and assistant.kind is ThreadEntryKind.ASSISTANT_MESSAGE
            and assistant.content == answer
            and observed.usage == facts.usage
            and observed.termination_reason == reason
            and observed.context_manifest == expected_manifest
        )

    async def _failed_turn_matches(
        self,
        turn: Turn,
        error_code: str,
        facts: ObservedTurnFacts,
        expected_manifest: tuple[dict[str, JsonValue], ...] | None,
    ) -> bool:
        if expected_manifest is None:
            return False
        current = await self._current_turn(turn)
        if current is None:
            return False
        _, observed = current
        return (
            observed.status is TurnStatus.FAILED
            and observed.error_code == error_code
            and observed.usage == facts.usage
            and observed.context_manifest == expected_manifest
        )

    async def _cancelled_turn_matches(
        self,
        turn: Turn,
        facts: ObservedTurnFacts,
        expected_manifest: tuple[dict[str, JsonValue], ...] | None,
    ) -> bool:
        if expected_manifest is None:
            return False
        current = await self._current_turn(turn)
        if current is None:
            return False
        _, observed = current
        return (
            observed.status is TurnStatus.CANCELLED
            and observed.termination_reason == "cancelled"
            and observed.usage == facts.usage
            and observed.context_manifest == expected_manifest
        )

    async def _expected_terminal_manifest(
        self,
        turn: Turn,
        facts: ObservedTurnFacts,
    ) -> tuple[dict[str, JsonValue], ...] | None:
        current = await self._current_turn(turn)
        if current is None:
            return None
        _, observed = current
        return facts.context_manifest or observed.context_manifest

    async def _current_turn(self, turn: Turn) -> tuple[ThreadView, Turn] | None:
        try:
            view = await self._conversation.read_thread(turn.thread_id)
        except (Exception, asyncio.CancelledError):
            return None
        observed = next((item for item in view.turns if item.id == turn.id), None)
        return (view, observed) if observed is not None else None

    async def _cleanup_turn(self, turn_id: str, *, seal: bool = True) -> None:
        try:
            if seal:
                await self._seal_changes(turn_id)
        finally:
            await self._checkpoints.delete(turn_id)

    async def _cleanup_committed_turn(
        self,
        turn_id: str,
        *,
        seal: bool = True,
    ) -> None:
        try:
            await self._cleanup_turn(turn_id, seal=seal)
        except (Exception, asyncio.CancelledError):
            logger.warning(
                "Turn cleanup failed after durable terminal state committed.",
                exc_info=True,
            )

    async def _fail_turn_preparation(
        self,
        turn: Turn,
        operation_id: str,
        projector: ApplicationEventProjector,
        error_code: str,
    ) -> None:
        await self._operations.commit_failed(
            operation_id,
            lambda: self._persist_failed_turn_and_cleanup(
                turn,
                error_code,
                ObservedTurnFacts(),
                seal=False,
            ),
        )
        with suppress(Exception, asyncio.CancelledError):
            await self._operations.publish_committed(
                operation_id,
                lambda: projector.turn_failed(error_code),
            )

    async def _finish_failed_active_turn(
        self,
        turn: Turn,
        operation_id: str,
        projector: ApplicationEventProjector,
        error_code: str,
        *,
        observe_facts: bool = False,
        seal: bool = True,
    ) -> None:
        async def persist_failure() -> None:
            facts = (
                await self._failure_observed_facts(turn.id)
                if observe_facts
                else ObservedTurnFacts()
            )
            await self._persist_failed_turn_and_cleanup(
                turn,
                error_code,
                facts,
                seal=seal,
            )

        await self._operations.commit_failed(operation_id, persist_failure)
        with suppress(Exception, asyncio.CancelledError):
            await self._operations.publish_committed(
                operation_id,
                lambda: projector.turn_failed(error_code),
            )

    async def _finish_cancelled_turn(
        self,
        turn: Turn,
        projector: ApplicationEventProjector,
    ) -> None:
        try:
            await finish_cancellation_safe(
                self._persist_cancelled_turn_and_cleanup(turn)
            )
        except BaseException:
            logger.warning(
                "Turn durable cancellation finalization failed while preserving "
                "the primary cancellation.",
                exc_info=True,
            )
            return
        await finish_bounded_cancellation_cleanup(
            projector.turn_cancelled("cancelled"),
            timeout_seconds=_CANCELLATION_EVENT_TIMEOUT_SECONDS,
        )

    async def _persist_cancelled_turn_and_cleanup(
        self,
        turn: Turn,
    ) -> None:
        facts = await self._cancellation_observed_facts(turn.id)
        await self._persist_cancelled_turn(turn, facts)
        await self._cleanup_committed_turn(turn.id)

    async def _cancellation_observed_facts(
        self,
        turn_id: str,
    ) -> ObservedTurnFacts:
        try:
            async with asyncio.timeout(_CANCELLATION_FACTS_TIMEOUT_SECONDS):
                return await self._latest_observed_facts(turn_id)
        except (Exception, asyncio.CancelledError):
            logger.warning(
                "Observed Turn facts were unavailable during cancellation.",
                exc_info=True,
            )
            return ObservedTurnFacts()

    async def _failure_observed_facts(self, turn_id: str) -> ObservedTurnFacts:
        try:
            async with asyncio.timeout(_CANCELLATION_FACTS_TIMEOUT_SECONDS):
                return await self._latest_observed_facts(turn_id)
        except (Exception, asyncio.CancelledError):
            logger.warning(
                "Observed Turn facts were unavailable after execution failure.",
                exc_info=True,
            )
            return ObservedTurnFacts()

    def _trim_tasks(self) -> None:
        while len(self._tasks) > 64:
            self._tasks.pop(next(iter(self._tasks)))

    def _task_completed(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()
        self._trim_tasks()

    async def _fail_recovery(
        self,
        turn: Turn,
        error_code: str,
        *,
        facts: ObservedTurnFacts | None = None,
    ) -> None:
        observed = facts or ObservedTurnFacts()
        await self._conversation.fail_turn(
            turn.id,
            error_code,
            usage=observed.usage,
            context_manifest=observed.context_manifest,
        )
        await self._cleanup_committed_turn(turn.id)
        await self._emit_recovery_events(turn, error_code=error_code)

    async def _emit_recovery_events(
        self,
        turn: Turn,
        *,
        error_code: str | None = None,
    ) -> None:
        try:
            projector = await self._recovery_projector(turn)
        except Exception:
            logger.warning(
                "Turn recovery event projection could not be initialized.",
                exc_info=True,
            )
            return
        terminal_event: Callable[[], Awaitable[None]] = (
            projector.turn_completed
            if error_code is None
            else lambda: projector.turn_failed(error_code)
        )
        for event_name, emit in (
            ("turn.started", projector.turn_started),
            ("turn.completed" if error_code is None else "turn.failed", terminal_event),
        ):
            try:
                await emit()
            except Exception:
                logger.warning(
                    "Turn recovery event delivery failed for %s.",
                    event_name,
                    exc_info=True,
                )

    async def _latest_observed_facts(self, turn_id: str) -> ObservedTurnFacts:
        try:
            state = await self._checkpoints.latest_state(turn_id)
        except CheckpointCorrupt:
            return ObservedTurnFacts()
        return observed_turn_facts(state)

    async def _recovery_projector(self, turn: Turn) -> ApplicationEventProjector:
        view = await self._conversation.read_thread(turn.thread_id)
        return ApplicationEventProjector(
            emitter=self._emitter,
            thread_id=turn.thread_id,
            turn_id=turn.id,
            operation_id=f"operation_recovery_{turn.id}",
            client_message_id=_client_message_id(view, turn),
        )

    def _require_thread_workspace(self, thread: Thread) -> None:
        if thread.workspace_key != self._workspace_key:
            raise TurnExecutionFailed("thread_workspace_mismatch")


def _client_message_id(view: ThreadView, turn: Turn) -> str:
    entry = next(item for item in view.entries if item.id == turn.user_entry_id)
    assert entry.client_message_id is not None
    return entry.client_message_id


async def _await_task_uninterruptibly[T](task: asyncio.Task[T]) -> T:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _reconcile_frozen_context_snapshot(
    state: AgentState,
    view: ThreadView,
    turn: Turn,
    validator: ContextSnapshotValidator,
    conversation: ConversationService,
) -> Turn | None:
    entry = next((item for item in view.entries if item.id == turn.user_entry_id), None)
    if entry is None or entry.kind is not ThreadEntryKind.USER_MESSAGE:
        return None
    checkpoint_manifest = tuple(state["context_manifest"])
    candidate = Turn.model_validate(
        turn.model_copy(update={"context_manifest": checkpoint_manifest}).model_dump()
    )
    if not frozen_context_snapshot_is_valid(state, turn=candidate, view=view):
        return None
    if not validator(state, turn=candidate, view=view):
        return None
    if turn.context_manifest == checkpoint_manifest:
        return turn
    if not frozen_context_manifests_share_lineage(
        checkpoint_manifest,
        turn.context_manifest,
    ):
        return None
    try:
        return await conversation.compare_and_swap_context_manifest(
            turn.id,
            checkpoint_manifest,
            expected_context_manifest=turn.context_manifest,
        )
    except ConversationConflict:
        current_view = await conversation.read_thread(turn.thread_id)
        current = next(item for item in current_view.turns if item.id == turn.id)
        if (
            current.status is TurnStatus.IN_PROGRESS
            and current.context_manifest == checkpoint_manifest
        ):
            return current
        raise


def _checkpoint_identity_is_valid(
    state: AgentState,
    *,
    view: ThreadView,
    turn: Turn,
    workspace_key: str,
) -> bool:
    return (
        view.thread.id == turn.thread_id
        and view.thread.workspace_key == workspace_key
        and state["workspace_key"] == workspace_key
        and state["thread_id"] == turn.thread_id
        and state["turn_id"] == turn.id
        and state["provider"] == turn.provider
        and state["model"] == turn.model
        and state["thinking_enabled"] == turn.thinking_enabled
        and _checkpoint_budget_state_is_valid(state, turn)
    )


def _checkpoint_budget_state_is_valid(state: AgentState, turn: Turn) -> bool:
    try:
        ModelUsage.model_validate(state["usage"])
    except ValueError:
        return False
    reason = state["termination_reason"]
    if reason is not None and (not reason.strip() or len(reason) > 128):
        return False
    executed_result_count = _executed_tool_result_count(state)
    return (
        executed_result_count is not None
        and 0 <= state["model_calls"] <= turn.budgets.model_calls
        and 0 <= state["tool_calls"] <= turn.budgets.tool_calls
        and 0 <= state["provider_retries"] <= turn.budgets.provider_retries
        and 0 <= state["compressions"] <= turn.budgets.compressions
        and state["active_execution_seconds"] >= 0
        and isfinite(state["active_execution_seconds"])
        and state["tool_calls"] >= executed_result_count
    )


def _executed_tool_result_count(state: AgentState) -> int | None:
    executed = 0
    skipped = False
    for raw_result in state["tool_results"]:
        try:
            result = ToolResult.model_validate(raw_result)
        except ValueError:
            return None
        if result.metadata.get("executed") is False:
            if not _valid_budget_skip_result(result, state["termination_reason"]):
                return None
            skipped = True
            continue
        if skipped:
            return None
        executed += 1
    return executed


def _valid_budget_skip_result(result: ToolResult, reason: str | None) -> bool:
    if reason not in _RESUMABLE_BUDGET_REASONS:
        return False
    message = f"Tool call was not executed: {reason}."
    return (
        result.status is ToolStatus.ERROR
        and result.content == message
        and result.metadata == {"executed": False, "reason": reason}
        and result.error is not None
        and result.error.code is ToolErrorCode.EXECUTION_FAILED
        and result.error.message == message
        and result.error.retryable is False
        and result.presentation is None
    )


def _resumable_budget_interruption(state: AgentState, turn: Turn) -> bool:
    reason = state["termination_reason"]
    return (
        reason in _RESUMABLE_BUDGET_REASONS
        and state["model_calls"] < turn.budgets.model_calls
        and bool(state["pending_tool_calls"])
        and any(
            result.get("metadata") == {"executed": False, "reason": reason}
            for result in state["tool_results"]
        )
    )


def _uncertain_tool_call(
    state: AgentState,
    replay_safety: ToolReplaySafetyResolver,
) -> bool:
    index = state["next_tool_index"]
    if index >= len(state["pending_tool_calls"]):
        return False
    call = state["pending_tool_calls"][index]
    name = call.get("name")
    call_id = call.get("call_id")
    completed_ids = {
        result.get("call_id")
        for result in state["tool_results"]
        if isinstance(result.get("call_id"), str)
    }
    if call_id in completed_ids:
        return False
    if not isinstance(name, str):
        return True
    try:
        return replay_safety(name) is not ToolReplaySafety.REPLAYABLE
    except Exception:
        return True
