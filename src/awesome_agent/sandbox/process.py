from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import cast

from awesome_agent.sandbox.base import CommandResult

_TERMINATION_GRACE_SECONDS = 2.0


async def run_process(
    arguments: Sequence[str],
    *,
    command_label: str,
    workspace: Path,
    timeout_seconds: float,
) -> CommandResult:
    process = _start_process(arguments, workspace=workspace)
    communicate = asyncio.create_task(
        asyncio.to_thread(process.communicate),
        name=f"communicate:{command_label}",
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            asyncio.shield(communicate),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        await _terminate_process(process, arguments)
        stdout, stderr = await _finish_communicate(communicate)
        return CommandResult(
            command=command_label,
            exit_code=-1,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
        )
    except asyncio.CancelledError:
        await _terminate_process(process, arguments)
        await _finish_communicate(communicate)
        raise
    return CommandResult(
        command=command_label,
        exit_code=process.returncode if process.returncode is not None else -1,
        stdout=stdout,
        stderr=stderr,
    )


def _start_process(
    arguments: Sequence[str],
    *,
    workspace: Path,
) -> subprocess.Popen[str]:
    if os.name == "nt":
        return subprocess.Popen(
            list(arguments),
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=cast(int, getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
        )
    return subprocess.Popen(
        list(arguments),
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


async def _terminate_process(
    process: subprocess.Popen[str],
    arguments: Sequence[str],
) -> None:
    if process.poll() is None:
        _terminate_process_group(process)
        try:
            await asyncio.to_thread(process.wait, timeout=_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_process_group(process)
            with suppress(subprocess.TimeoutExpired):
                await asyncio.to_thread(
                    process.wait,
                    timeout=_TERMINATION_GRACE_SECONDS,
                )
    container_name = _docker_container_name(arguments)
    if container_name is not None:
        await asyncio.to_thread(_remove_docker_container, container_name)


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        process.terminate()
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)  # type: ignore[attr-defined]


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        process.kill()
        return
    with suppress(ProcessLookupError):
        sigkill = cast(int, getattr(signal, "SIGKILL", signal.SIGTERM))
        os.killpg(process.pid, sigkill)  # type: ignore[attr-defined]


async def _finish_communicate(task: asyncio.Task[tuple[str, str]]) -> tuple[str, str]:
    with suppress(asyncio.CancelledError):
        return await asyncio.shield(task)
    return "", ""


def _docker_container_name(arguments: Sequence[str]) -> str | None:
    if len(arguments) < 4 or list(arguments[:2]) != ["docker", "run"]:
        return None
    for index, value in enumerate(arguments):
        if value == "--name" and index + 1 < len(arguments):
            return arguments[index + 1]
    return None


def _remove_docker_container(container_name: str) -> None:
    with suppress(subprocess.SubprocessError, OSError):
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
