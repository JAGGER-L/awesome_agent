import asyncio
import os
import sys
from pathlib import Path

import pytest

from awesome_agent.core.tools.process import ProcessRunner


@pytest.mark.asyncio
async def test_process_runner_completes_and_bounds_output(tmp_path: Path) -> None:
    result = await ProcessRunner().run(
        argv=[sys.executable, "-c", "print('x' * 100)"],
        cwd=tmp_path,
        environment=dict(os.environ),
        timeout_seconds=5,
        max_output_chars=10,
    )

    assert result.exit_code == 0
    assert result.stdout == "x" * 10
    assert result.stdout_truncated is True
    assert result.stderr == ""
    assert result.timed_out is False
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_process_runner_times_out_and_terminates(tmp_path: Path) -> None:
    result = await ProcessRunner().run(
        argv=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        environment=dict(os.environ),
        timeout_seconds=0.1,
        max_output_chars=1_000,
    )

    assert result.exit_code == -1
    assert result.timed_out is True


@pytest.mark.asyncio
async def test_process_runner_cancellation_terminates_quickly(tmp_path: Path) -> None:
    task = asyncio.create_task(
        ProcessRunner().run(
            argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            environment=dict(os.environ),
            timeout_seconds=60,
            max_output_chars=1_000,
        )
    )
    await asyncio.sleep(0.1)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
