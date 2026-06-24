from pathlib import Path

import pytest

from awesome_agent.sandbox.docker import DockerSandbox

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_docker_sandbox_executes_in_workspace(tmp_path: Path) -> None:
    sandbox = DockerSandbox(image="postgres:17-alpine")

    result = await sandbox.execute(
        "printf sandbox-ok",
        workspace=tmp_path,
        timeout_seconds=30,
    )

    assert result.exit_code == 0
    assert result.stdout == "sandbox-ok"
