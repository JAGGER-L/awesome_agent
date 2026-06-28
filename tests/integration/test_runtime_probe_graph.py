from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import ExecutionKind
from awesome_agent.domain.models import Run
from awesome_agent.persistence.checkpoints import checkpoint_saver
from awesome_agent.runtime.probe_graph import (
    RUNTIME_PROBE_ROUTE,
    RuntimeProbeGraph,
    RuntimeProbeState,
)

pytestmark = pytest.mark.integration


class DeterministicCrash(RuntimeError):
    pass


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL" not in os.environ,
    reason="Checkpoint database is not configured.",
)
async def test_runtime_probe_resumes_from_durable_checkpoint(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run = Run(
        id=uuid4(),
        goal="Runtime probe",
        execution_kind=ExecutionKind.RUNTIME_PROBE,
        runtime_route=RUNTIME_PROBE_ROUTE,
        graph_thread_id=f"run:{uuid4()}",
        workspace_path=workspace,
    )
    crashed = False

    async def fault(node: str, _: RuntimeProbeState) -> None:
        nonlocal crashed
        if node == "checkpoint_probe" and not crashed:
            crashed = True
            raise DeterministicCrash

    async with checkpoint_saver(
        os.environ["AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL"]
    ) as saver:
        await saver.setup()
        graph = RuntimeProbeGraph(saver, fault_hook=fault)
        with pytest.raises(DeterministicCrash):
            await graph.execute(run)

        state, resumed = await graph.execute(run)
        repeated, reconciled = await graph.execute(run)

    assert resumed
    assert reconciled
    assert state["completed_steps"] == [
        "initialize",
        "checkpoint_probe",
        "finalize",
    ]
    assert repeated == state
