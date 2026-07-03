from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from uuid import UUID

import pytest

from awesome_agent.domain.enums import (
    DispatchStatus,
    ExecutionKind,
    RunIntent,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run, RunLease
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE
from awesome_agent.runtime.probe_graph import RuntimeProbeState
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig


class FakeRepository:
    def __init__(self, run: Run | None = None, agents: list[Agent] | None = None):
        self.run = run
        self.agents = agents or []

    async def get_run(self, run_id: UUID) -> Run:
        if self.run is None:
            raise KeyError(run_id)
        return self.run

    async def list_agents(self, run_id: UUID) -> list[Agent]:
        return self.agents


class FakeDispatcher:
    def __init__(self, lease: RunLease | None = None) -> None:
        self.lease = lease
        self.calls: list[tuple[str, object]] = []

    async def claim_next(self, **kwargs: object) -> RunLease | None:
        self.calls.append(("claim", kwargs))
        lease, self.lease = self.lease, None
        return lease


class FakeProbeGraph:
    async def execute(self, run: Run) -> tuple[RuntimeProbeState, bool]:
        return (
            {
                "run_id": str(run.id),
                "runtime_route": "runtime-probe",
                "phase": "completed",
                "completed_steps": [],
                "result_summary": "done",
            },
            False,
        )


class FakeConversationGraph:
    async def execute(self, run: Run, leader: Agent) -> dict[str, object]:
        return {
            "run_id": str(run.id),
            "agent_id": str(leader.id),
            "runtime_route": CONVERSATION_TURN_ROUTE,
            "phase": "completed",
            "final_answer": "hello",
            "result_summary": "conversation done",
        }


def test_worker_advertises_conversation_route() -> None:
    worker = _make_worker(conversation_graph=FakeConversationGraph())

    routes = {route.route for route in worker._supported_runtime_routes()}

    assert CONVERSATION_TURN_ROUTE in routes


def test_worker_validates_conversation_graph_route() -> None:
    worker = _make_worker(conversation_graph=FakeConversationGraph())

    worker._validate_run(_conversation_route_run())


@pytest.mark.asyncio
async def test_worker_claim_filter_includes_conversation_kind() -> None:
    dispatcher = FakeDispatcher()
    worker = _make_worker(
        dispatcher=dispatcher,
        conversation_graph=FakeConversationGraph(),
    )

    assert not await worker.run_once()
    claim = dispatcher.calls[0][1]

    assert isinstance(claim, dict)
    assert CONVERSATION_TURN_ROUTE in claim["runtime_routes"]
    assert ExecutionKind.CONVERSATION in claim["execution_kinds"]


def _make_worker(
    *,
    dispatcher: FakeDispatcher | None = None,
    conversation_graph: FakeConversationGraph | None = None,
) -> DurableWorker:
    return DurableWorker(
        dispatcher=cast(Any, dispatcher or FakeDispatcher()),
        repository=FakeRepository(),  # type: ignore[arg-type]
        probe_graph=FakeProbeGraph(),  # type: ignore[arg-type]
        conversation_graph=conversation_graph,  # type: ignore[arg-type]
        config=_config(),
    )


def _conversation_route_run() -> Run:
    return Run(
        goal="hello",
        intent=RunIntent.CONVERSATION,
        execution_kind=ExecutionKind.CONVERSATION,
        runtime_route=CONVERSATION_TURN_ROUTE,
        status=RunStatus.CREATED,
        dispatch_status=DispatchStatus.QUEUED,
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
