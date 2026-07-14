from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from awesome_agent.application import composition
from awesome_agent.application.contracts import InitializeStatus
from awesome_agent.core.events import CollectingEventSink
from awesome_agent.storage.state_lease import (
    StateLease,
    StateLeaseMode,
    StateLeaseUnavailable,
)


def test_shared_lease_in_another_process_blocks_reset_ownership(
    tmp_path: Path,
) -> None:
    script = """
import sys
from pathlib import Path
from awesome_agent.storage.state_lease import StateLease, StateLeaseMode

lease = StateLease.acquire(Path(sys.argv[1]), StateLeaseMode.SHARED)
print("ready", flush=True)
sys.stdin.readline()
lease.close()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(StateLeaseUnavailable):
            StateLease.acquire(tmp_path, StateLeaseMode.EXCLUSIVE)
    finally:
        if child.stdin is not None:
            child.stdin.write("stop\n")
            child.stdin.flush()
            child.stdin.close()
        child.wait(timeout=10)

    assert child.returncode == 0
    lease = StateLease.acquire(tmp_path, StateLeaseMode.EXCLUSIVE)
    lease.close()


@pytest.mark.asyncio
async def test_composed_applications_hold_shared_lease_until_shutdown(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    first = await composition.compose_local_application(
        home=home,
        workspace=first_workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    second = await composition.compose_local_application(
        home=home,
        workspace=second_workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    first_result = await first.initialize()
    second_result = await second.initialize()

    assert first_result.ok and first_result.value is not None
    assert second_result.ok and second_result.value is not None
    assert first_result.value.status is InitializeStatus.TRUST_REQUIRED
    assert second_result.value.status is InitializeStatus.TRUST_REQUIRED
    with pytest.raises(StateLeaseUnavailable):
        StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)

    await first.shutdown()
    with pytest.raises(StateLeaseUnavailable):
        StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)

    await second.shutdown()
    lease = StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)
    lease.close()
