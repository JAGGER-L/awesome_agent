from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from awesome_agent.domain.enums import ExecutionKind
from awesome_agent.domain.models import Run
from awesome_agent.runtime.dispatch import (
    CorruptRuntimeStateError,
    IncompatibleGraphError,
)
from awesome_agent.runtime.probe_graph import (
    RUNTIME_PROBE_ROUTE,
    RuntimeProbeGraph,
    RuntimeProbeState,
)


class FakeSaver:
    def __init__(self, checkpoint: object) -> None:
        self.checkpoint = checkpoint

    async def aget_tuple(self, _: object) -> object:
        return self.checkpoint


class FakeGraph:
    def __init__(
        self,
        *,
        next_nodes: tuple[str, ...],
        state: RuntimeProbeState,
    ) -> None:
        self.next_nodes = next_nodes
        self.state = state
        self.inputs: list[object] = []

    async def ainvoke(self, value: object, *_: object, **__: object) -> object:
        self.inputs.append(value)
        return self.state

    async def aget_state(self, _: object) -> object:
        return SimpleNamespace(next=self.next_nodes, values=self.state)


def _run(tmp_path: Path) -> Run:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return Run(
        goal="probe",
        execution_kind=ExecutionKind.RUNTIME_PROBE,
        runtime_route=RUNTIME_PROBE_ROUTE,
        graph_thread_id="run:test",
        workspace_path=workspace,
    )


def _state(run: Run) -> RuntimeProbeState:
    return {
        "run_id": str(run.id),
        "runtime_route": RUNTIME_PROBE_ROUTE,
        "phase": "completed",
        "completed_steps": ["initialize", "checkpoint_probe", "finalize"],
        "result_summary": "done",
    }


def _graph(
    *,
    saver: FakeSaver,
    graph: FakeGraph,
) -> RuntimeProbeGraph:
    probe = RuntimeProbeGraph.__new__(RuntimeProbeGraph)
    probe.saver = saver  # type: ignore[assignment]
    probe.graph = graph  # type: ignore[assignment]
    probe.fault_hook = None
    return probe


@pytest.mark.asyncio
async def test_execute_starts_new_and_resumes_existing_checkpoint(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    new_graph = FakeGraph(next_nodes=(), state=_state(run))
    new = _graph(saver=FakeSaver(None), graph=new_graph)

    state, resumed = await new.execute(run)

    assert state["phase"] == "completed"
    assert not resumed
    assert isinstance(new_graph.inputs[0], dict)

    resumed_graph = FakeGraph(
        next_nodes=("checkpoint_probe",),
        state=_state(run),
    )
    resumed_probe = _graph(
        saver=FakeSaver(object()),
        graph=resumed_graph,
    )
    _, did_resume = await resumed_probe.execute(run)
    assert did_resume
    assert resumed_graph.inputs == [None]


@pytest.mark.asyncio
async def test_execute_reconciles_completed_checkpoint(tmp_path: Path) -> None:
    run = _run(tmp_path)
    graph = FakeGraph(next_nodes=(), state=_state(run))
    probe = _graph(saver=FakeSaver(object()), graph=graph)

    state, recovered = await probe.execute(run)

    assert recovered
    assert state["result_summary"] == "done"
    assert graph.inputs == []


@pytest.mark.asyncio
async def test_probe_nodes_build_stable_state() -> None:
    observed: list[str] = []

    async def hook(node: str, _: RuntimeProbeState) -> None:
        observed.append(node)

    probe = RuntimeProbeGraph.__new__(RuntimeProbeGraph)
    probe.fault_hook = hook
    initial: RuntimeProbeState = {
        "run_id": "run",
        "runtime_route": RUNTIME_PROBE_ROUTE,
        "phase": "created",
        "completed_steps": [],
    }

    initialized = await probe._initialize(initial)
    checkpointed = await probe._checkpoint_probe(initialized)
    completed = await probe._finalize(checkpointed)

    assert observed == ["initialize", "checkpoint_probe", "finalize"]
    assert completed["completed_steps"] == [
        "initialize",
        "checkpoint_probe",
        "finalize",
    ]


def test_probe_rejects_incompatible_or_corrupt_run(tmp_path: Path) -> None:
    probe = RuntimeProbeGraph.__new__(RuntimeProbeGraph)
    incompatible = _run(tmp_path).model_copy(update={"runtime_route": "other-graph"})
    with pytest.raises(IncompatibleGraphError):
        probe._validate_run(incompatible)

    missing_workspace = _run(tmp_path).model_copy(
        update={"workspace_path": tmp_path / "missing"}
    )
    with pytest.raises(CorruptRuntimeStateError):
        probe._validate_run(missing_workspace)
