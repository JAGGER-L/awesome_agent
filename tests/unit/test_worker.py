from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from awesome_agent.domain.enums import AgentKind, EventType, ExecutionKind, RunIntent
from awesome_agent.domain.models import Agent, Run, RunLease, RuntimeEvent
from awesome_agent.observability.facade import ObservabilityFacade
from awesome_agent.observability.repository import InMemoryObservabilityRepository
from awesome_agent.persistence.budget import (
    ContextCompactionRecord,
    RunBudgetLedgerRecord,
)
from awesome_agent.runtime.dispatch import (
    ApprovalInterrupt,
    CorruptRuntimeStateError,
    PermanentExecutionError,
)
from awesome_agent.runtime.graphs import (
    MODIFYING_CODING_ROUTE,
    RUNTIME_PROBE_ROUTE,
    SCOPED_TEAM_CODING_ROUTE,
    TEAM_CODING_ROUTE,
    TEAM_ROLE_ROUTE,
    TEAM_VERIFIER_ROUTE,
)
from awesome_agent.runtime.probe_graph import RuntimeProbeState
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig
from awesome_agent.runtime.worker_heartbeats import RuntimeRoute, WorkerHeartbeat


class FakeRepository:
    def __init__(self, run: Run, agents: list[Agent] | None = None) -> None:
        self.run = run
        self.agents = agents or []

    async def get_run(self, _: UUID) -> Run:
        return self.run

    async def list_agents(self, _: UUID) -> list[Any]:
        return self.agents


class FakeDispatcher:
    def __init__(self, lease: RunLease | None) -> None:
        self.lease = lease
        self.calls: list[tuple[str, object]] = []
        self.cancel_requested = False

    async def claim_next(self, **kwargs: object) -> RunLease | None:
        self.calls.append(("claim", kwargs))
        lease, self.lease = self.lease, None
        return lease

    async def heartbeat(self, lease: RunLease, **_: object) -> RunLease:
        self.calls.append(("heartbeat", lease))
        return lease

    async def start_execution(self, lease: RunLease, **_: object) -> None:
        self.calls.append(("start", lease))

    async def complete_execution(self, lease: RunLease, **kwargs: object) -> None:
        self.calls.append(("complete", kwargs))

    async def release_for_retry(self, lease: RunLease, **kwargs: object) -> None:
        self.calls.append(("retry", kwargs))

    async def request_cancellation(
        self,
        *,
        run_id: UUID,
        requested_by: str | None,
        reason: str | None,
    ) -> RuntimeEvent | None:
        self.calls.append(
            (
                "request_cancellation",
                {
                    "run_id": run_id,
                    "requested_by": requested_by,
                    "reason": reason,
                },
            )
        )
        return None

    async def is_cancel_requested(self, lease: RunLease) -> bool:
        self.calls.append(("cancel_check", lease))
        return self.cancel_requested

    async def mark_cancelled(self, lease: RunLease, **kwargs: object) -> None:
        self.calls.append(("cancelled", kwargs))

    async def release_for_approval_wait(
        self,
        lease: RunLease,
        **kwargs: object,
    ) -> None:
        self.calls.append(("approval_wait", kwargs))

    async def release_for_child_wait(
        self,
        lease: RunLease,
        **kwargs: object,
    ) -> None:
        self.calls.append(("child_wait", kwargs))

    async def requeue_after_approval(self, **kwargs: object) -> None:
        self.calls.append(("approval_requeue", kwargs))

    async def expire_pending_approvals(self, **kwargs: object) -> int:
        self.calls.append(("expire_approvals", kwargs))
        return 0

    async def mark_recovery_required(self, lease: RunLease, **kwargs: object) -> None:
        self.calls.append(("recovery", kwargs))

    async def fail_execution(self, lease: RunLease, **kwargs: object) -> None:
        self.calls.append(("failed", kwargs))

    async def recover_expired(self, **kwargs: object) -> int:
        self.calls.append(("recover_expired", kwargs))
        return 0

    async def append_fenced_event(
        self,
        lease: RunLease,
        *,
        event_type: EventType,
        payload: dict[str, object],
        transition_id: str | None = None,
    ) -> RuntimeEvent:
        event = RuntimeEvent(
            run_id=lease.run_id,
            sequence=sum(call[0] == "event" for call in self.calls) + 1,
            event_type=event_type,
            payload=payload,
            transition_id=transition_id,
            trace_id=lease.run_id.hex,
        )
        self.calls.append(("event", event))
        return event

    async def release_to_queue(self, *_: object, **__: object) -> None:
        raise NotImplementedError


class FakeWorkerHeartbeatRepository:
    def __init__(self) -> None:
        self.heartbeats: list[WorkerHeartbeat] = []

    async def upsert(self, heartbeat: WorkerHeartbeat) -> None:
        self.heartbeats.append(heartbeat)

    async def list_recent(self, *, stale_after: datetime) -> list[WorkerHeartbeat]:
        return [
            heartbeat
            for heartbeat in self.heartbeats
            if heartbeat.heartbeat_at >= stale_after
        ]

    async def mark_stopping(self, worker_id: UUID) -> None:
        return None


class FakeBudgetRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID]] = []

    async def upsert_ledger(
        self,
        ledger: RunBudgetLedgerRecord,
    ) -> RunBudgetLedgerRecord:
        return ledger

    async def get_ledger(self, run_id: UUID) -> RunBudgetLedgerRecord:
        return RunBudgetLedgerRecord(run_id=run_id)

    async def record_compaction(
        self,
        compaction: ContextCompactionRecord,
    ) -> ContextCompactionRecord:
        return compaction

    async def list_compactions(self, run_id: UUID) -> list[ContextCompactionRecord]:
        return []

    async def open_active_window(
        self,
        run_id: UUID,
        now: datetime,
    ) -> RunBudgetLedgerRecord:
        self.calls.append(("open", run_id))
        return RunBudgetLedgerRecord(run_id=run_id, active_window_started_at=now)

    async def close_active_window(
        self,
        run_id: UUID,
        now: datetime,
    ) -> RunBudgetLedgerRecord:
        self.calls.append(("close", run_id))
        return RunBudgetLedgerRecord(run_id=run_id, active_seconds=1)


class FakeGraph:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def execute(self, _: Run) -> tuple[RuntimeProbeState, bool]:
        if self.error is not None:
            raise self.error
        return (
            {
                "run_id": "run",
                "runtime_route": "runtime-probe",
                "phase": "completed",
                "completed_steps": ["initialize", "checkpoint_probe", "finalize"],
                "result_summary": "done",
            },
            False,
        )


class SlowGraph:
    async def execute(self, _: Run) -> tuple[RuntimeProbeState, bool]:
        await asyncio.sleep(60)
        raise AssertionError("slow graph should be cancelled")


class FakeModifyingGraph:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def execute(
        self,
        _: Run,
        __: Agent,
        *,
        event_sink: object | None = None,
    ) -> tuple[dict[str, object], bool]:
        if self.error is not None:
            raise self.error
        return (
            {
                "run_id": "run",
                "agent_id": "agent",
                "runtime_route": MODIFYING_CODING_ROUTE,
                "messages": [],
                "model_turn_count": 1,
                "tool_call_count": 2,
                "successful_writes": 1,
                "final_diff_after_write": True,
                "phase": "completed",
                "final_answer": "Changed README.md; validation not run.",
                "result_summary": "modifying done",
            },
            False,
        )


class FakeTeamGraph:
    async def execute(
        self,
        _: Run,
        __: Agent,
        *,
        repository: object,
        event_sink: object | None = None,
    ) -> tuple[dict[str, object], bool]:
        if repository is None:
            raise AssertionError("repository is required")
        if not callable(event_sink):
            raise AssertionError("event sink is required")
        return (
            {
                "run_id": "run",
                "agent_id": "agent",
                "runtime_route": SCOPED_TEAM_CODING_ROUTE,
                "phase": "completed",
                "final_answer": "Team completed after verification.",
                "result_summary": "team done",
            },
            False,
        )


class EmittingTeamGraph(FakeTeamGraph):
    async def execute(
        self,
        run: Run,
        agent: Agent,
        *,
        repository: object,
        event_sink: object | None = None,
    ) -> tuple[dict[str, object], bool]:
        if not callable(event_sink):
            raise AssertionError("event sink is required")
        await event_sink(
            EventType.MODEL_CALL_CREATED,
            {
                "turn": 1,
                "status": "completed",
                "stop_reason": "completed",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "input_tokens": 10,
                "output_tokens": 20,
                "latency_ms": 31,
            },
            "model-turn:1",
        )
        await event_sink(
            EventType.TOOL_CALL_CREATED,
            {
                "turn": 1,
                "call_id": "call-1",
                "tool": "repo.diff",
                "status": "completed",
                "latency_ms": 7,
                "sandbox": "docker",
            },
            "tool:1:call-1",
        )
        return await super().execute(
            run,
            agent,
            repository=repository,
            event_sink=event_sink,
        )


class EmittingModifyingGraph(FakeModifyingGraph):
    async def execute(
        self,
        run: Run,
        agent: Agent,
        *,
        event_sink: object | None = None,
    ) -> tuple[dict[str, object], bool]:
        if not callable(event_sink):
            raise AssertionError("event sink is required")
        await event_sink(
            EventType.MODEL_CALL_CREATED,
            {
                "turn": 1,
                "status": "completed",
                "stop_reason": "completed",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "input_tokens": 10,
                "output_tokens": 20,
                "latency_ms": 31,
            },
            "model-turn:1",
        )
        await event_sink(
            EventType.TOOL_CALL_CREATED,
            {
                "turn": 1,
                "call_id": "call-1",
                "tool": "repo.diff",
                "status": "completed",
                "latency_ms": 7,
                "sandbox": "docker",
            },
            "tool:1:call-1",
        )
        return await super().execute(run, agent, event_sink=event_sink)


class RecordingExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def _lease() -> RunLease:
    now = datetime.now(UTC)
    return RunLease(
        run_id=uuid4(),
        worker_id=uuid4(),
        worker_name="worker",
        fencing_token=1,
        attempt=1,
        lease_acquired_at=now,
        lease_expires_at=now + timedelta(seconds=60),
        heartbeat_at=now,
    )


def _run(lease: RunLease) -> Run:
    return Run(
        id=lease.run_id,
        goal="probe",
        execution_kind=ExecutionKind.RUNTIME_PROBE,
        runtime_route="runtime-probe",
        graph_thread_id=f"run:{lease.run_id}",
    )


def _modifying_run(lease: RunLease) -> Run:
    return Run(
        id=lease.run_id,
        goal="modify",
        intent=RunIntent.MODIFYING,
        execution_kind=ExecutionKind.CODING,
        runtime_route=MODIFYING_CODING_ROUTE,
        graph_thread_id=f"run:{lease.run_id}",
    )


def _team_run(lease: RunLease) -> Run:
    return Run(
        id=lease.run_id,
        goal="team",
        intent=RunIntent.MODIFYING,
        execution_kind=ExecutionKind.CODING,
        runtime_route=SCOPED_TEAM_CODING_ROUTE,
        graph_thread_id=f"run:{lease.run_id}",
    )


def _config() -> WorkerConfig:
    return WorkerConfig(
        lease_duration=timedelta(seconds=60),
        heartbeat_interval=timedelta(seconds=15),
        poll_interval=0.01,
        recovery_interval=15,
        shutdown_grace=0.01,
        retry_delay=timedelta(seconds=5),
        max_attempts=3,
    )


def _facade(
    repository: InMemoryObservabilityRepository,
    exporter: RecordingExporter,
) -> ObservabilityFacade:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return ObservabilityFacade(
        repository=repository,
        tracer=provider.get_tracer("test"),
    )


@pytest.mark.asyncio
async def test_worker_claims_and_completes_probe() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_run(lease)),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        config=_config(),
    )

    assert await worker.run_once()
    assert [call[0] for call in dispatcher.calls] == [
        "claim",
        "start",
        "complete",
    ]


@pytest.mark.asyncio
async def test_worker_retries_transient_graph_error() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_run(lease)),  # type: ignore[arg-type]
        probe_graph=FakeGraph(RuntimeError("temporary")),  # type: ignore[arg-type]
        config=_config(),
    )

    await worker.run_once()

    assert dispatcher.calls[-1][0] == "retry"


@pytest.mark.asyncio
async def test_worker_marks_active_run_cancelled() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    dispatcher.cancel_requested = True
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_run(lease)),  # type: ignore[arg-type]
        probe_graph=SlowGraph(),  # type: ignore[arg-type]
        config=_config(),
    )

    assert await worker.run_once()
    calls = [call[0] for call in dispatcher.calls]

    assert "cancelled" in calls
    assert "complete" not in calls
    assert "retry" not in calls


@pytest.mark.asyncio
async def test_worker_marks_corrupt_state_for_recovery() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_run(lease)),  # type: ignore[arg-type]
        probe_graph=FakeGraph(CorruptRuntimeStateError("corrupt")),  # type: ignore[arg-type]
        config=_config(),
    )

    await worker.run_once()

    assert dispatcher.calls[-1][0] == "recovery"


@pytest.mark.asyncio
async def test_worker_fails_permanent_graph_error() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_run(lease)),  # type: ignore[arg-type]
        probe_graph=FakeGraph(PermanentExecutionError("permanent")),  # type: ignore[arg-type]
        config=_config(),
    )

    await worker.run_once()

    assert dispatcher.calls[-1][0] == "failed"


@pytest.mark.asyncio
async def test_worker_forever_recovers_and_stops() -> None:
    dispatcher = FakeDispatcher(None)

    async def stop_sleep(_: float) -> None:
        worker.request_stop()
        await asyncio.sleep(0)

    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(Run(goal="unused")),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        config=_config(),
        sleep=stop_sleep,
    )

    await worker.run_forever()

    assert [call[0] for call in dispatcher.calls] == [
        "recover_expired",
        "expire_approvals",
        "claim",
    ]


@pytest.mark.asyncio
async def test_worker_claims_modifying_graph_when_configured() -> None:
    dispatcher = FakeDispatcher(None)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(Run(goal="unused")),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        modifying_graph=object(),  # type: ignore[arg-type]
        config=_config(),
    )

    assert not await worker.run_once()
    claim = dispatcher.calls[0][1]

    assert isinstance(claim, dict)
    assert MODIFYING_CODING_ROUTE in claim["runtime_routes"]


@pytest.mark.asyncio
async def test_worker_claims_scoped_team_graph_when_configured() -> None:
    dispatcher = FakeDispatcher(None)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(Run(goal="unused")),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        team_graph=object(),  # type: ignore[arg-type]
        config=_config(),
    )

    assert not await worker.run_once()
    claim = dispatcher.calls[0][1]

    assert isinstance(claim, dict)
    assert SCOPED_TEAM_CODING_ROUTE in claim["runtime_routes"]


@pytest.mark.asyncio
async def test_worker_advertises_distributed_team_graphs_when_configured() -> None:
    dispatcher = FakeDispatcher(None)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(Run(goal="unused")),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        team_leader_graph=object(),  # type: ignore[arg-type]
        team_role_graph=object(),  # type: ignore[arg-type]
        team_verifier_graph=object(),  # type: ignore[arg-type]
        config=_config(),
    )

    assert not await worker.run_once()
    claim = dispatcher.calls[0][1]

    assert isinstance(claim, dict)
    assert TEAM_CODING_ROUTE in claim["runtime_routes"]
    assert TEAM_ROLE_ROUTE in claim["runtime_routes"]
    assert TEAM_VERIFIER_ROUTE in claim["runtime_routes"]


@pytest.mark.asyncio
async def test_worker_marks_unsupported_team_graph_for_recovery() -> None:
    lease = _lease()
    run = _team_run(lease).model_copy(update={"runtime_route": TEAM_CODING_ROUTE})
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(run),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        team_graph=FakeTeamGraph(),  # type: ignore[arg-type]
        config=_config(),
    )

    await worker.run_once()

    assert dispatcher.calls[-1][0] == "recovery"


@pytest.mark.asyncio
async def test_worker_upserts_heartbeat_before_claiming() -> None:
    dispatcher = FakeDispatcher(None)
    heartbeats = FakeWorkerHeartbeatRepository()
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(Run(goal="unused")),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        config=_config(),
        heartbeat_repository=heartbeats,
    )

    assert not await worker.run_once()

    assert heartbeats.heartbeats[0].worker_id == worker.worker_id
    assert heartbeats.heartbeats[0].supported_runtime_routes == [
        RuntimeRoute(RUNTIME_PROBE_ROUTE)
    ]


@pytest.mark.asyncio
async def test_worker_executes_modifying_graph_with_validated_completion() -> None:
    lease = _lease()
    run = _modifying_run(lease)
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(run, [leader]),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        modifying_graph=FakeModifyingGraph(),  # type: ignore[arg-type]
        config=_config(),
    )

    assert await worker.run_once()
    complete = dispatcher.calls[-1]

    assert complete[0] == "complete"
    assert isinstance(complete[1], dict)
    assert complete[1]["completion_kind"] == "modifying_validated"
    assert complete[1]["result_text"] == "Changed README.md; validation not run."


@pytest.mark.asyncio
async def test_worker_tracks_active_budget_window_for_coding_graph() -> None:
    lease = _lease()
    run = _modifying_run(lease)
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    dispatcher = FakeDispatcher(lease)
    budgets = FakeBudgetRepository()
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(run, [leader]),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        modifying_graph=FakeModifyingGraph(),  # type: ignore[arg-type]
        config=_config(),
        budget_repository=budgets,
    )

    assert await worker.run_once()

    assert budgets.calls == [("open", run.id), ("close", run.id)]


@pytest.mark.asyncio
async def test_worker_closes_active_budget_window_for_approval_wait() -> None:
    lease = _lease()
    run = _modifying_run(lease)
    approval_id = uuid4()
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    dispatcher = FakeDispatcher(lease)
    budgets = FakeBudgetRepository()
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(run, [leader]),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        modifying_graph=FakeModifyingGraph(ApprovalInterrupt(approval_id)),  # type: ignore[arg-type]
        config=_config(),
        budget_repository=budgets,
    )

    assert await worker.run_once()

    assert budgets.calls == [("open", run.id), ("close", run.id)]
    assert dispatcher.calls[-1][0] == "approval_wait"


@pytest.mark.asyncio
async def test_worker_executes_team_graph_with_validated_completion() -> None:
    lease = _lease()
    run = _team_run(lease)
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(run, [leader]),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        team_graph=FakeTeamGraph(),  # type: ignore[arg-type]
        config=_config(),
    )

    assert await worker.run_once()
    complete = dispatcher.calls[-1]

    assert complete[0] == "complete"
    assert isinstance(complete[1], dict)
    assert complete[1]["completion_kind"] == "team_validated"
    assert complete[1]["result_text"] == "Team completed after verification."


@pytest.mark.asyncio
async def test_worker_records_boundary_spans_through_observability_facade() -> None:
    lease = _lease()
    run = _modifying_run(lease)
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    dispatcher = FakeDispatcher(lease)
    observability = InMemoryObservabilityRepository()
    exporter = RecordingExporter()
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(run, [leader]),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        modifying_graph=EmittingModifyingGraph(),  # type: ignore[arg-type]
        config=_config(),
        observability=_facade(observability, exporter),
        observability_repository=observability,
    )

    assert await worker.run_once()

    spans = await observability.list_spans_for_run(run.id)
    metrics = await observability.list_metrics_for_run(run.id)
    model_calls = await observability.list_model_calls_for_run(run.id)

    assert {span.name for span in spans} == {
        "run.execute",
        "graph.execute",
    }
    assert {span.name for span in exporter.spans} == {"run.execute", "graph.execute"}
    assert not model_calls
    assert any(metric.name == "run.duration_ms" for metric in metrics)


@pytest.mark.asyncio
async def test_worker_keeps_event_projection_for_unmigrated_team_routes() -> None:
    lease = _lease()
    run = _team_run(lease)
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    dispatcher = FakeDispatcher(lease)
    observability = InMemoryObservabilityRepository()
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(run, [leader]),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        team_graph=EmittingTeamGraph(),  # type: ignore[arg-type]
        config=_config(),
        observability_repository=observability,
    )

    assert await worker.run_once()

    spans = await observability.list_spans_for_run(run.id)
    metrics = await observability.list_metrics_for_run(run.id)
    model_calls = await observability.list_model_calls_for_run(run.id)

    assert {span.name for span in spans} >= {
        "run.execute",
        "graph.execute",
        "model.call",
        "tool.call",
        "sandbox.execute",
    }
    assert any(metric.name == "model.latency_ms" for metric in metrics)
    assert any(metric.name == "tool.duration_ms" for metric in metrics)
    assert model_calls[0].model == "deepseek-v4-flash"
    assert model_calls[0].latency_ms == 31


@pytest.mark.asyncio
async def test_worker_releases_modifying_run_for_approval_wait() -> None:
    lease = _lease()
    run = _modifying_run(lease)
    approval_id = uuid4()
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(run, [leader]),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        modifying_graph=FakeModifyingGraph(ApprovalInterrupt(approval_id)),  # type: ignore[arg-type]
        config=_config(),
    )

    assert await worker.run_once()
    release = dispatcher.calls[-1]

    assert release[0] == "approval_wait"
    assert isinstance(release[1], dict)
    assert release[1]["approval_id"] == approval_id
    assert release[1]["reason"] == "approval_wait"


@pytest.mark.asyncio
async def test_worker_marks_coding_run_without_leader_for_recovery() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_modifying_run(lease)),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        modifying_graph=FakeModifyingGraph(),  # type: ignore[arg-type]
        config=_config(),
    )

    await worker.run_once()

    assert dispatcher.calls[-1][0] == "recovery"


@pytest.mark.asyncio
async def test_worker_rejects_unconfigured_coding_graph() -> None:
    lease = _lease()
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=FakeRepository(_modifying_run(lease)),  # type: ignore[arg-type]
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        config=_config(),
    )

    await worker.run_once()

    assert dispatcher.calls[-1][0] == "recovery"
