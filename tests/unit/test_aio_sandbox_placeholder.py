from pathlib import Path

import pytest

from awesome_agent.sandbox.aio import AioDockerSandbox, AioDockerSandboxUnavailable
from awesome_agent.sandbox.base import CommandRequest


@pytest.mark.asyncio
async def test_aio_sandbox_placeholder_fails_clearly(tmp_path: Path) -> None:
    sandbox = AioDockerSandbox(base_url="http://127.0.0.1:8765")

    with pytest.raises(AioDockerSandboxUnavailable, match="Task 62"):
        await sandbox.execute(
            CommandRequest(
                argv=["python", "--version"],
                workspace=tmp_path,
                timeout_seconds=30,
            )
        )
