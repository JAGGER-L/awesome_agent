from pathlib import Path

import pytest

from awesome_agent.sandbox.base import CommandRequest
from awesome_agent.sandbox.local import LocalSandbox

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_local_sandbox_executes_only_when_selected(tmp_path: Path) -> None:
    sandbox = LocalSandbox()

    result = await sandbox.execute(
        CommandRequest(
            argv=["Write-Output", "local-ok"],
            workspace=tmp_path,
            timeout_seconds=10,
        )
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "local-ok"
    assert result.sandbox == "local"


@pytest.mark.asyncio
async def test_local_sandbox_enforces_timeout(tmp_path: Path) -> None:
    sandbox = LocalSandbox()

    result = await sandbox.execute(
        CommandRequest(
            argv=["Start-Sleep", "-Seconds", "2"],
            workspace=tmp_path,
            timeout_seconds=0.1,
        )
    )

    assert result.timed_out
    assert result.exit_code == -1
