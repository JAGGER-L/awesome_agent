from __future__ import annotations

import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e


SNAKE_HTML = """<!doctype html>
<html>
<head><title>Snake</title></head>
<body>
<h1>Snake</h1>
<canvas id="board" width="320" height="320"></canvas>
<script>
const canvas = document.getElementById('board');
const ctx = canvas.getContext('2d');
let direction = 'right';
document.addEventListener('keydown', (event) => {
  if (event.key === 'ArrowUp') direction = 'up';
  if (event.key === 'ArrowDown') direction = 'down';
  if (event.key === 'ArrowLeft') direction = 'left';
  if (event.key === 'ArrowRight') direction = 'right';
});
ctx.fillText('Snake direction: ' + direction, 20, 20);
</script>
</body>
</html>
"""


def test_aio_sandbox_writes_snake_html_to_thread_workspace(tmp_path: Path) -> None:
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
    run_id = str(uuid.uuid4())
    user_data = tmp_path / "threads" / "thread-snake" / "user-data"
    workspace = user_data / "workspace"
    artifacts = tmp_path / "runs" / run_id / "artifacts"
    workspace.mkdir(parents=True)
    artifacts.mkdir(parents=True)
    (artifacts / "snake-evidence.txt").write_text(
        "deterministic AIO sandbox snake verification",
        encoding="utf-8",
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
            f"{user_data}:/mnt/user-data",
            "awesome-agent-sandbox:aio",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    try:
        _wait_for_sandbox()
        response = httpx.post(
            "http://127.0.0.1:8765/execute",
            json={
                "argv": [
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "Path('/mnt/user-data/workspace/snake.html')"
                        ".write_text("
                        f"{SNAKE_HTML!r}, encoding='utf-8')"
                    ),
                ],
                "workspace": "/mnt/user-data/workspace",
                "timeout_seconds": 30,
            },
            timeout=40,
        )
        response.raise_for_status()

        html = (workspace / "snake.html").read_text(encoding="utf-8")
        assert response.json()["exit_code"] == 0
        assert "<canvas" in html
        assert "Snake" in html
        assert "keydown" in html
        assert (artifacts / "snake-evidence.txt").is_file()
    finally:
        subprocess.run(["docker", "rm", "-f", container], check=False, timeout=10)


def _wait_for_sandbox() -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            response = httpx.get("http://127.0.0.1:8765/health", timeout=2)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(1)
    pytest.fail("sandbox did not become healthy")
