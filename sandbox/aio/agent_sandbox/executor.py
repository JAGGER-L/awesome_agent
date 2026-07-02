from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from sandbox.aio.agent_sandbox.models import ExecuteRequest, ExecuteResponse


async def execute_command(request: ExecuteRequest) -> ExecuteResponse:
    workspace = Path(request.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(request.environment)
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            request.argv,
            cwd=workspace,
            env=env,
            capture_output=True,
            check=False,
            timeout=request.timeout_seconds,
        )
        stdout_bytes = completed.stdout or b""
        stderr_bytes = completed.stderr or b""
        exit_code = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as error:
        stdout_bytes = _timeout_output(error.stdout)
        stderr_bytes = _timeout_output(error.stderr)
        exit_code = -1
        timed_out = True
    stdout, stdout_truncated = _decode_and_bound(
        stdout_bytes,
        request.max_output_chars,
    )
    stderr, stderr_truncated = _decode_and_bound(
        stderr_bytes,
        request.max_output_chars,
    )
    return ExecuteResponse(
        command=" ".join(request.argv),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def _decode_and_bound(raw: bytes, limit: int) -> tuple[str, bool]:
    text = raw.decode("utf-8", errors="replace")
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _timeout_output(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="replace")
