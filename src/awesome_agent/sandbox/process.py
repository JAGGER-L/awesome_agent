from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Sequence
from pathlib import Path

from awesome_agent.sandbox.base import CommandResult


async def run_process(
    arguments: Sequence[str],
    *,
    command_label: str,
    workspace: Path,
    timeout_seconds: float,
) -> CommandResult:
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            list(arguments),
            cwd=workspace,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        return CommandResult(
            command=command_label,
            exit_code=-1,
            stdout=_timeout_output(error.stdout),
            stderr=_timeout_output(error.stderr),
            timed_out=True,
        )
    return CommandResult(
        command=command_label,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _timeout_output(output: bytes | str | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode(errors="replace")
    return output
