from pathlib import Path

import httpx
import pytest

from awesome_agent.sandbox.aio import AioDockerSandbox
from awesome_agent.sandbox.base import CommandRequest


@pytest.mark.asyncio
async def test_aio_client_posts_execute_request(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "command": "python --version",
                "exit_code": 0,
                "stdout": "Python 3.12",
                "stderr": "",
                "timed_out": False,
                "stdout_truncated": False,
                "stderr_truncated": False,
            },
        )

    sandbox = AioDockerSandbox(
        base_url="http://sandbox:8765",
        transport=httpx.MockTransport(handler),
    )

    result = await sandbox.execute(
        CommandRequest(
            argv=["python", "--version"],
            workspace=tmp_path,
            timeout_seconds=30,
        )
    )

    assert result.sandbox == "aio-docker"
    assert result.stdout == "Python 3.12"
    assert requests[0].url.path == "/execute"
    assert requests[0].method == "POST"
    assert requests[0].read()


@pytest.mark.asyncio
async def test_aio_client_reports_http_error(tmp_path: Path) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "not ready"})

    sandbox = AioDockerSandbox(
        base_url="http://sandbox:8765",
        transport=httpx.MockTransport(handler),
    )

    result = await sandbox.execute(
        CommandRequest(
            argv=["python", "--version"],
            workspace=tmp_path,
            timeout_seconds=30,
        )
    )

    assert result.exit_code == -1
    assert result.sandbox == "aio-docker"
    assert "503" in result.stderr
