from __future__ import annotations

import asyncio
import codecs
import locale
import os
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

_TERMINATION_GRACE_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: float


class ShellExecutionBackend(Protocol):
    async def run(
        self,
        *,
        argv: list[str],
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
        max_output_chars: int,
    ) -> ProcessResult: ...


async def _drain(
    stream: asyncio.StreamReader | None,
    max_chars: int,
) -> tuple[str, bool]:
    if stream is None:
        return "", False
    encoding = locale.getpreferredencoding(False) if os.name == "nt" else "utf-8"
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    chunks: list[str] = []
    retained = 0
    truncated = False
    while data := await stream.read(64 * 1024):
        text = decoder.decode(data)
        remaining = max_chars - retained
        if len(text) > remaining:
            if remaining > 0:
                chunks.append(text[:remaining])
                retained += remaining
            truncated = True
        else:
            chunks.append(text)
            retained += len(text)
    final = decoder.decode(b"", final=True)
    remaining = max_chars - retained
    if len(final) > remaining:
        if remaining > 0:
            chunks.append(final[:remaining])
        truncated = True
    else:
        chunks.append(final)
    return "".join(chunks), truncated


def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        creationflags = cast(int, getattr(subprocess, "CREATE_NO_WINDOW", 0))
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            creationflags=creationflags,
        )
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)  # type: ignore[attr-defined]


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        process.kill()
        return
    with suppress(ProcessLookupError):
        sigkill = cast(int, getattr(signal, "SIGKILL", signal.SIGTERM))
        os.killpg(process.pid, sigkill)  # type: ignore[attr-defined]


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    await asyncio.to_thread(_terminate_process_group, process)
    try:
        await asyncio.wait_for(
            process.wait(),
            timeout=_TERMINATION_GRACE_SECONDS,
        )
    except TimeoutError:
        _kill_process_group(process)
        with suppress(TimeoutError):
            await asyncio.wait_for(
                process.wait(),
                timeout=_TERMINATION_GRACE_SECONDS,
            )


class ProcessRunner:
    async def run(
        self,
        *,
        argv: list[str],
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
        max_output_chars: int,
    ) -> ProcessResult:
        process_options: dict[str, Any] = {}
        if os.name == "nt":
            process_options["creationflags"] = cast(
                int,
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            process_options["start_new_session"] = True

        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **process_options,
        )
        stdout_task = asyncio.create_task(_drain(process.stdout, max_output_chars))
        stderr_task = asyncio.create_task(_drain(process.stderr, max_output_chars))
        timed_out = False
        try:
            async with asyncio.timeout(timeout_seconds):
                exit_code = await process.wait()
        except TimeoutError:
            timed_out = True
            exit_code = -1
            await _terminate(process)
        except asyncio.CancelledError:
            await _terminate(process)
            await asyncio.gather(stdout_task, stderr_task)
            raise

        (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.gather(
            stdout_task,
            stderr_task,
        )
        duration_ms = (time.perf_counter() - started) * 1_000
        return ProcessResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            duration_ms=duration_ms,
        )
