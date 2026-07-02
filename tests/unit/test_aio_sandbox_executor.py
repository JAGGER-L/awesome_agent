from pathlib import Path

import pytest
from sandbox.aio.agent_sandbox.executor import execute_command
from sandbox.aio.agent_sandbox.models import ExecuteRequest


@pytest.mark.asyncio
async def test_execute_command_runs_in_workspace(tmp_path: Path) -> None:
    response = await execute_command(
        ExecuteRequest(
            argv=[
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('ok.txt').write_text('ok'); print('done')"
                ),
            ],
            workspace=str(tmp_path),
        )
    )

    assert response.exit_code == 0
    assert "done" in response.stdout
    assert (tmp_path / "ok.txt").read_text(encoding="utf-8") == "ok"


@pytest.mark.asyncio
async def test_execute_command_bounds_output(tmp_path: Path) -> None:
    response = await execute_command(
        ExecuteRequest(
            argv=["python", "-c", "print('x' * 2000)"],
            workspace=str(tmp_path),
            max_output_chars=1000,
        )
    )

    assert len(response.stdout) == 1000
    assert response.stdout_truncated
