from pathlib import Path

import pytest

from awesome_agent.sandbox.local import TrustedLocalSandbox

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_trusted_local_executes_only_when_selected(tmp_path: Path) -> None:
    sandbox = TrustedLocalSandbox()

    result = await sandbox.execute(
        "Write-Output local-ok",
        workspace=tmp_path,
        timeout_seconds=10,
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "local-ok"


@pytest.mark.asyncio
async def test_trusted_local_enforces_timeout(tmp_path: Path) -> None:
    sandbox = TrustedLocalSandbox()

    result = await sandbox.execute(
        "Start-Sleep -Seconds 2",
        workspace=tmp_path,
        timeout_seconds=0.1,
    )

    assert result.timed_out
    assert result.exit_code == -1
