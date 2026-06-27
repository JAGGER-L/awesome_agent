from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

from awesome_agent.domain.enums import EventType, ExecutionKind
from awesome_agent.domain.models import Agent, Run, RunLease, RuntimeEvent
from awesome_agent.observability.repository import (
    DurableMetric,
    DurableModelCall,
    DurableSpan,
    NoopObservabilityRepository,
    ObservabilityRepository,
)
from awesome_agent.runtime.dispatch import (
    ApprovalInterrupt,
    CorruptRuntimeStateError,
    IncompatibleGraphError,
    LeaseLost,
    PermanentExecutionError,
    RunCancelled,
    RunDispatcher,
)
from awesome_agent.runtime.graphs import (
    MODIFYING_CODING_GRAPH,
    MODIFYING_CODING_VERSION,
    READ_ONLY_CODING_GRAPH,
    READ_ONLY_CODING_VERSION,
    RUNTIME_PROBE_GRAPH,
    RUNTIME_PROBE_VERSION,
    TEAM_CODING_GRAPH,
    TEAM_CODING_VERSION,
)
from awesome_agent.runtime.modifying_graph import (
    ModifyingAgentState,
    ModifyingCodingGraph,
)
from awesome_agent.runtime.probe_graph import RuntimeProbeGraph, RuntimeProbeState
from awesome_agent.runtime.readonly_graph import ReadOnlyAgentState, ReadOnlyCodingGraph
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.runtime.team_graph import TeamCodingGraph, TeamCodingState
from awesome_agent.runtime.worker_heartbeats import (
    GraphIdentity,
    WorkerHeartbeat,
    WorkerHeartbeatRepository,
    WorkerHeartbeatStatus,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    lease_duration: timedelta
    heartbeat_interval: timedelta
    poll_interval: float
    recovery_interval: float
    shutdown_grace: float
    retry_delay: timedelta
    max_attempts: int


class DurableWorker:
    def __init__(
        self,
        *,
        dispatcher: RunDispatcher,
        repository: RuntimeRepository,
        probe_graph: RuntimeProbeGraph,
        coding_graph: ReadOnlyCodingGraph | None = None,
        modifying_graph: ModifyingCodingGraph | None = None,
        team_graph: TeamCodingGraph | None = None,
        config: WorkerConfig,
        worker_id: UUID | None = None,
        worker_name: str | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        observability_repository: ObservabilityRepository | None = None,
        heartbeat_repository: WorkerHeartbeatRepository | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.repository = repository
        self.probe_graph = probe_graph
        self.coding_graph = coding_graph
        self.modifying_graph = modifying_graph
        self.team_graph = team_graph
        self.config = config
        self.worker_id = worker_id or uuid4()
        self.worker_name = worker_name or default_worker_name()
        self.sleep = sleep
        self.stop_requested = asyncio.Event()
        self.observability_repository = (
            observability_repository or NoopObservabilityRepository()
        )
        self.heartbeat_repository = heartbeat_repository
        self.started_at = datetime.now(UTC)

    def request_stop(self) -> None:
        self.stop_requested.set()

    async def run_forever(self) -> None:
        next_recovery = 0.0
        try:
            while not self.stop_requested.is_set():
                if monotonic() >= next_recovery:
                    await self.dispatcher.recover_expired(
                        max_attempts=self.config.max_attempts
                    )
                    await self.dispatcher.expire_pending_approvals()
                    next_recovery = monotonic() + self.config.recovery_interval
                processed = await self.run_once()
                if not processed and not self.stop_requested.is_set():
                    await self.sleep(self.config.poll_interval)
        finally:
            await self._mark_worker_stopping()

    async def run_once(self) -> bool:
        await self._upsert_worker_heartbeat()
        graph_identities = {
            (graph.name, graph.version) for graph in self._supported_graph_identities()
        }
        execution_kinds = {ExecutionKind.RUNTIME_PROBE}
        if self.coding_graph is not None:
            graph_identities.add((READ_ONLY_CODING_GRAPH, READ_ONLY_CODING_VERSION))
            execution_kinds.add(ExecutionKind.CODING)
        if self.modifying_graph is not None:
            graph_identities.add((MODIFYING_CODING_GRAPH, MODIFYING_CODING_VERSION))
            execution_kinds.add(ExecutionKind.CODING)
        if self.team_graph is not None:
            graph_identities.add((TEAM_CODING_GRAPH, TEAM_CODING_VERSION))
            execution_kinds.add(ExecutionKind.CODING)
        lease = await self.dispatcher.claim_next(
            worker_id=self.worker_id,
            worker_name=self.worker_name,
            lease_duration=self.config.lease_duration,
            max_attempts=self.config.max_attempts,
            execution_kinds=frozenset(execution_kinds),
            graph_identities=frozenset(graph_identities),
        )
        if lease is None:
            return False
        await self._execute_claim(lease)
        return True

    async def mark_stopping(self) -> None:
        await self._mark_worker_stopping()

    def _supported_graph_identities(self) -> list[GraphIdentity]:
        identities = [GraphIdentity(RUNTIME_PROBE_GRAPH, RUNTIME_PROBE_VERSION)]
        if self.coding_graph is not None:
            identities.append(
                GraphIdentity(READ_ONLY_CODING_GRAPH, READ_ONLY_CODING_VERSION)
            )
        if self.modifying_graph is not None:
            identities.append(
                GraphIdentity(MODIFYING_CODING_GRAPH, MODIFYING_CODING_VERSION)
            )
        if self.team_graph is not None:
            identities.append(GraphIdentity(TEAM_CODING_GRAPH, TEAM_CODING_VERSION))
        return identities

    async def _upsert_worker_heartbeat(self) -> None:
        if self.heartbeat_repository is None:
            return
        try:
            await self.heartbeat_repository.upsert(
                WorkerHeartbeat(
                    worker_id=self.worker_id,
                    worker_name=self.worker_name,
                    started_at=self.started_at,
                    heartbeat_at=datetime.now(UTC),
                    supported_graphs=self._supported_graph_identities(),
                    status=WorkerHeartbeatStatus.ONLINE,
                )
            )
        except Exception:
            logger.exception("Worker heartbeat write failed.")

    async def _mark_worker_stopping(self) -> None:
        if self.heartbeat_repository is None:
            return
        try:
            await self.heartbeat_repository.mark_stopping(self.worker_id)
        except Exception:
            logger.exception("Worker heartbeat stopping update failed.")

    async def _execute_claim(self, lease: RunLease) -> None:
        started_at = datetime.now(UTC)
        started = monotonic()
        status = "completed"
        error_text: str | None = None
        run = await self.repository.get_run(lease.run_id)
        try:
            self._validate_run(run)
            await self.dispatcher.start_execution(
                lease,
                graph_name=run.graph_name or "",
                graph_version=run.graph_version or 0,
            )
            state, recovered = await self._execute_with_heartbeat(run, lease)
            is_coding = run.execution_kind is ExecutionKind.CODING
            final_answer = state.get("final_answer") if is_coding else None
            completion_kind = self._completion_kind(run)
            await self.dispatcher.complete_execution(
                lease,
                result_summary=state.get(
                    "result_summary",
                    "Execution completed.",
                ),
                recovered=recovered,
                completion_kind=completion_kind,
                goal_executed=is_coding,
                result_text=(final_answer if isinstance(final_answer, str) else None),
            )
        except LeaseLost:
            status = "lease_lost"
            return
        except RunCancelled:
            status = "cancelled"
            return
        except ApprovalInterrupt as interrupt:
            status = "waiting_approval"
            error_text = str(interrupt)
            await self._release_for_approval_if_owned(lease, interrupt.approval_id)
        except (IncompatibleGraphError, CorruptRuntimeStateError) as error:
            status = "recovery_required"
            error_text = str(error)
            await self._mark_recovery_if_owned(lease, str(error))
        except PermanentExecutionError as error:
            status = "failed"
            error_text = str(error)
            await self._fail_if_owned(lease, str(error))
        except asyncio.CancelledError:
            status = "cancelled"
            error_text = "Worker task was cancelled."
            raise
        except Exception as error:
            status = "retry_scheduled"
            error_text = str(error)
            await self._retry_if_owned(lease, error)
        finally:
            await self._record_span_and_metric(
                run_id=lease.run_id,
                name="run.execute",
                category="run",
                status=status,
                started_at=started_at,
                started=started,
                attributes={
                    "worker_id": str(self.worker_id),
                    "worker_name": self.worker_name,
                    "attempt": lease.attempt,
                },
                error=error_text,
            )

    async def _execute_with_heartbeat(
        self,
        run: Run,
        lease: RunLease,
    ) -> tuple[
        RuntimeProbeState | ReadOnlyAgentState | ModifyingAgentState | TeamCodingState,
        bool,
    ]:
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat_loop(lease, lease_lost),
            name=f"heartbeat:{lease.run_id}",
        )
        graph_task = asyncio.create_task(
            self._execute_graph(run, lease),
            name=f"graph:{lease.run_id}",
        )
        stop_task = asyncio.create_task(
            self.stop_requested.wait(),
            name=f"stop:{lease.run_id}",
        )
        lost_task = asyncio.create_task(
            lease_lost.wait(),
            name=f"lease-lost:{lease.run_id}",
        )
        cancel_task = asyncio.create_task(
            self._cancel_watch_loop(lease),
            name=f"cancel-watch:{lease.run_id}",
        )
        try:
            done, _ = await asyncio.wait(
                {graph_task, stop_task, lost_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if graph_task in done:
                return await graph_task
            if cancel_task in done:
                graph_task.cancel()
                try:
                    await asyncio.wait_for(
                        _consume_cancel(graph_task),
                        timeout=self.config.shutdown_grace,
                    )
                except TimeoutError as error:
                    await self._mark_recovery_if_owned(
                        lease,
                        "Cancellation did not reach a safe graph boundary.",
                    )
                    raise RunCancelled() from error
                await self._cancel_if_owned(lease, "cancel_requested")
                raise RunCancelled()
            if lost_task in done and lease_lost.is_set():
                graph_task.cancel()
                await _consume_cancel(graph_task)
                raise LeaseLost(f"Lease lost for Run {lease.run_id}.")
            try:
                return await asyncio.wait_for(
                    asyncio.shield(graph_task),
                    timeout=self.config.shutdown_grace,
                )
            except TimeoutError as error:
                graph_task.cancel()
                await _consume_cancel(graph_task)
                raise LeaseLost(
                    f"Worker stopped before Run {lease.run_id} reached a safe boundary."
                ) from error
        finally:
            heartbeat.cancel()
            stop_task.cancel()
            lost_task.cancel()
            cancel_task.cancel()
            await asyncio.gather(
                heartbeat,
                stop_task,
                lost_task,
                cancel_task,
                return_exceptions=True,
            )

    async def _heartbeat_loop(
        self,
        lease: RunLease,
        lease_lost: asyncio.Event,
    ) -> None:
        safety_window = (
            self.config.lease_duration - self.config.heartbeat_interval
        ).total_seconds()
        last_confirmed = monotonic()
        while not lease_lost.is_set():
            await self.sleep(self.config.heartbeat_interval.total_seconds())
            try:
                lease = await self.dispatcher.heartbeat(
                    lease,
                    lease_duration=self.config.lease_duration,
                )
                last_confirmed = monotonic()
            except LeaseLost:
                lease_lost.set()
            except Exception:
                if monotonic() - last_confirmed >= safety_window:
                    lease_lost.set()
                else:
                    await self.sleep(1)

    async def _cancel_watch_loop(self, lease: RunLease) -> None:
        while True:
            await self.sleep(min(1.0, self.config.poll_interval))
            try:
                if await self.dispatcher.is_cancel_requested(lease):
                    return
            except LeaseLost:
                return

    async def _retry_if_owned(self, lease: RunLease, error: Exception) -> None:
        try:
            await self.dispatcher.release_for_retry(
                lease,
                delay=self.config.retry_delay,
                reason=type(error).__name__,
                error=str(error),
                max_attempts=self.config.max_attempts,
            )
        except LeaseLost:
            return

    async def _release_for_approval_if_owned(
        self,
        lease: RunLease,
        approval_id: UUID,
    ) -> None:
        try:
            await self.dispatcher.release_for_approval_wait(
                lease,
                approval_id=approval_id,
                reason="approval_wait",
            )
        except LeaseLost:
            return

    async def _mark_recovery_if_owned(
        self,
        lease: RunLease,
        reason: str,
    ) -> None:
        try:
            await self.dispatcher.mark_recovery_required(lease, reason=reason)
        except LeaseLost:
            return

    async def _cancel_if_owned(self, lease: RunLease, reason: str) -> None:
        try:
            await self.dispatcher.mark_cancelled(lease, reason=reason)
        except LeaseLost:
            return

    async def _fail_if_owned(self, lease: RunLease, reason: str) -> None:
        try:
            await self.dispatcher.fail_execution(lease, reason=reason)
        except LeaseLost:
            return

    def _validate_run(self, run: Run) -> None:
        if run.execution_kind is not ExecutionKind.CODING:
            return
        if run.graph_name == READ_ONLY_CODING_GRAPH and self.coding_graph is not None:
            return
        if (
            run.graph_name == MODIFYING_CODING_GRAPH
            and self.modifying_graph is not None
        ):
            return
        if run.graph_name == TEAM_CODING_GRAPH and self.team_graph is not None:
            return
        raise IncompatibleGraphError(
            "Worker has no compatible Coding graph configured."
        )

    async def _execute_graph(
        self,
        run: Run,
        lease: RunLease,
    ) -> tuple[
        RuntimeProbeState | ReadOnlyAgentState | ModifyingAgentState | TeamCodingState,
        bool,
    ]:
        started_at = datetime.now(UTC)
        started = monotonic()
        status = "completed"
        error_text: str | None = None
        try:
            if run.execution_kind is ExecutionKind.RUNTIME_PROBE:
                return await self.probe_graph.execute(run)
            if run.execution_kind is ExecutionKind.CODING:
                agents = await self.repository.list_agents(run.id)
                leader = next(
                    (agent for agent in agents if agent.parent_agent_id is None),
                    None,
                )
                if leader is None:
                    raise CorruptRuntimeStateError("Coding Run has no Leader.")

                async def emit(
                    event_type: EventType,
                    payload: dict[str, object],
                    transition_id: str,
                ) -> None:
                    event = await self.dispatcher.append_fenced_event(
                        lease,
                        event_type=event_type,
                        payload=payload,
                        transition_id=transition_id,
                    )
                    await self._record_event_observability(run, leader, event)

                if run.graph_name == READ_ONLY_CODING_GRAPH and self.coding_graph:
                    return await self.coding_graph.execute(
                        run,
                        leader,
                        event_sink=emit,
                    )
                if run.graph_name == MODIFYING_CODING_GRAPH and self.modifying_graph:
                    return await self.modifying_graph.execute(
                        run,
                        leader,
                        event_sink=emit,
                    )
                if run.graph_name == TEAM_CODING_GRAPH and self.team_graph:
                    return await self.team_graph.execute(
                        run,
                        leader,
                        repository=self.repository,
                        event_sink=emit,
                    )
            raise IncompatibleGraphError(
                f"Worker cannot execute kind {run.execution_kind.value}."
            )
        except Exception as error:
            status = "failed"
            error_text = str(error)
            raise
        finally:
            await self._record_span_and_metric(
                run_id=run.id,
                name="graph.execute",
                category="graph",
                status=status,
                started_at=started_at,
                started=started,
                attributes={
                    "graph_name": run.graph_name,
                    "graph_version": run.graph_version,
                    "execution_kind": run.execution_kind.value,
                },
                error=error_text,
            )

    async def _record_event_observability(
        self,
        run: Run,
        leader: Agent,
        event: RuntimeEvent,
    ) -> None:
        if event.event_type is EventType.MODEL_CALL_CREATED:
            await self._record_model_call_event(run, leader, event)
        elif event.event_type is EventType.TOOL_CALL_CREATED:
            await self._record_tool_call_event(run, event)

    async def _record_model_call_event(
        self,
        run: Run,
        leader: Agent,
        event: RuntimeEvent,
    ) -> None:
        payload = event.payload
        latency_ms = _int_payload(payload, "latency_ms")
        span_id = _span_id()
        status = _str_payload(payload, "status", "unknown")
        agent_id = _uuid_payload(payload, "agent_id") or leader.id
        await self._record_best_effort(
            self.observability_repository.record_span(
                DurableSpan(
                    run_id=run.id,
                    trace_id=event.trace_id or run.id.hex,
                    span_id=span_id,
                    parent_span_id=None,
                    name="model.call",
                    category="model",
                    status=status,
                    ended_at=event.created_at,
                    duration_ms=latency_ms,
                    attributes={
                        "turn": _int_payload(payload, "turn"),
                        "agent_id": str(agent_id),
                        "provider": _str_payload(payload, "provider", "unknown"),
                        "model": _str_payload(payload, "model", leader.model),
                        "stop_reason": _str_payload(payload, "stop_reason", ""),
                    },
                    error=_str_payload(payload, "error", "") or None,
                )
            )
        )
        await self._record_best_effort(
            self.observability_repository.record_model_call(
                DurableModelCall(
                    run_id=run.id,
                    agent_id=agent_id,
                    turn=_int_payload(payload, "turn") or 0,
                    provider=_str_payload(payload, "provider", "unknown"),
                    model=_str_payload(payload, "model", leader.model),
                    status=status,
                    stop_reason=_str_payload(payload, "stop_reason", "") or None,
                    input_tokens=_int_payload(payload, "input_tokens"),
                    output_tokens=_int_payload(payload, "output_tokens"),
                    reasoning_tokens=_int_payload(payload, "reasoning_tokens"),
                    cache_read_tokens=_int_payload(payload, "cache_read_tokens"),
                    cache_write_tokens=_int_payload(payload, "cache_write_tokens"),
                    latency_ms=latency_ms,
                    trace_id=event.trace_id or run.id.hex,
                    span_id=span_id,
                    error=_str_payload(payload, "error", "") or None,
                )
            )
        )
        if latency_ms is not None:
            await self._record_metric(
                run.id,
                "model.latency_ms",
                latency_ms,
                "ms",
                {"status": status, "model": _str_payload(payload, "model", "")},
            )

    async def _record_tool_call_event(
        self,
        run: Run,
        event: RuntimeEvent,
    ) -> None:
        payload = event.payload
        latency_ms = _int_payload(payload, "latency_ms")
        status = _str_payload(payload, "status", "unknown")
        attributes = {
            "turn": _int_payload(payload, "turn"),
            "tool": _str_payload(payload, "tool", "unknown"),
            "call_id": _str_payload(payload, "call_id", ""),
            "sandbox": _str_payload(payload, "sandbox", ""),
        }
        await self._record_best_effort(
            self.observability_repository.record_span(
                DurableSpan(
                    run_id=run.id,
                    trace_id=event.trace_id or run.id.hex,
                    span_id=_span_id(),
                    parent_span_id=None,
                    name="tool.call",
                    category="tool",
                    status=status,
                    ended_at=event.created_at,
                    duration_ms=latency_ms,
                    attributes=attributes,
                    error=_str_payload(payload, "error", "") or None,
                )
            )
        )
        sandbox = attributes["sandbox"]
        if sandbox:
            await self._record_best_effort(
                self.observability_repository.record_span(
                    DurableSpan(
                        run_id=run.id,
                        trace_id=event.trace_id or run.id.hex,
                        span_id=_span_id(),
                        parent_span_id=None,
                        name="sandbox.execute",
                        category="sandbox",
                        status=status,
                        ended_at=event.created_at,
                        duration_ms=latency_ms,
                        attributes=attributes,
                        error=_str_payload(payload, "error", "") or None,
                    )
                )
            )
        if latency_ms is not None:
            await self._record_metric(
                run.id,
                "tool.duration_ms",
                latency_ms,
                "ms",
                {"status": status, "tool": attributes["tool"]},
            )

    async def _record_span_and_metric(
        self,
        *,
        run_id: UUID,
        name: str,
        category: str,
        status: str,
        started_at: datetime,
        started: float,
        attributes: dict[str, object],
        error: str | None,
    ) -> None:
        ended_at = datetime.now(UTC)
        duration_ms = max(0, int((monotonic() - started) * 1000))
        await self._record_best_effort(
            self.observability_repository.record_span(
                DurableSpan(
                    run_id=run_id,
                    trace_id=run_id.hex,
                    span_id=_span_id(),
                    parent_span_id=None,
                    name=name,
                    category=category,
                    status=status,
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=duration_ms,
                    attributes=attributes,
                    error=error,
                )
            )
        )
        await self._record_metric(
            run_id,
            f"{category}.duration_ms",
            duration_ms,
            "ms",
            {"status": status, **attributes},
        )

    async def _record_metric(
        self,
        run_id: UUID,
        name: str,
        value: float,
        unit: str,
        attributes: dict[str, object],
    ) -> None:
        await self._record_best_effort(
            self.observability_repository.record_metric(
                DurableMetric(
                    run_id=run_id,
                    name=name,
                    value=value,
                    unit=unit,
                    attributes=attributes,
                )
            )
        )

    async def _record_best_effort(self, action: Awaitable[object]) -> None:
        try:
            await action
        except Exception:
            logger.exception("Observability write failed.")

    def _completion_kind(self, run: Run) -> str:
        if run.execution_kind is not ExecutionKind.CODING:
            return "runtime_probe"
        if run.graph_name == TEAM_CODING_GRAPH:
            return "team_validated"
        if run.graph_name == MODIFYING_CODING_GRAPH:
            return "modifying_validated"
        return "read_only_coding"


async def _consume_cancel(task: asyncio.Task[object]) -> None:
    with suppress(asyncio.CancelledError):
        await task


def default_worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _span_id() -> str:
    return uuid4().hex[:16]


def _str_payload(
    payload: dict[str, object],
    key: str,
    default: str,
) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else default


def _int_payload(payload: dict[str, object], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _uuid_payload(payload: dict[str, object], key: str) -> UUID | None:
    value = payload.get(key)
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None
