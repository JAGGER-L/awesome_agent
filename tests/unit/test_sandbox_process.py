from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from awesome_agent.sandbox.process import run_process


@pytest.mark.asyncio
async def test_run_process_completes(tmp_path: Path) -> None:
    result = await run_process(
        [sys.executable, "-c", "print('ok')"],
        command_label="python",
        workspace=tmp_path,
        timeout_seconds=5,
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"


@pytest.mark.asyncio
async def test_run_process_times_out_and_terminates(tmp_path: Path) -> None:
    result = await run_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        command_label="sleep",
        workspace=tmp_path,
        timeout_seconds=0.1,
    )

    assert result.exit_code == -1
    assert result.timed_out is True


@pytest.mark.asyncio
async def test_run_process_cancellation_terminates_quickly(tmp_path: Path) -> None:
    task = asyncio.create_task(
        run_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            command_label="sleep",
            workspace=tmp_path,
            timeout_seconds=60,
        )
    )
    await asyncio.sleep(0.1)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
