import subprocess
import time

import httpx
import pytest

pytestmark = pytest.mark.integration


def test_aio_sandbox_container_executes_command() -> None:
    subprocess.run(
        [
            "docker",
            "build",
            "-f",
            "sandbox/aio/Dockerfile",
            "-t",
            "awesome-agent-sandbox:aio",
            ".",
        ],
        check=True,
        timeout=180,
    )
    container = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "-p",
            "8765:8765",
            "-v",
            "awesome_agent_test_user_data:/mnt/user-data",
            "awesome-agent-sandbox:aio",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                response = httpx.get("http://127.0.0.1:8765/health", timeout=2)
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(1)
        else:
            pytest.fail("sandbox did not become healthy")

        response = httpx.post(
            "http://127.0.0.1:8765/execute",
            json={
                "argv": ["python", "-c", "print('sandbox-ok')"],
                "workspace": "/mnt/user-data/workspace",
                "timeout_seconds": 30,
            },
            timeout=40,
        )

        assert response.status_code == 200
        assert response.json()["stdout"].strip() == "sandbox-ok"
    finally:
        subprocess.run(["docker", "rm", "-f", container], check=False, timeout=10)
