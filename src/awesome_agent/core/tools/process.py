from __future__ import annotations

import asyncio
import codecs
import locale
import os
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

_TERMINATION_GRACE_SECONDS = 2.0
_FORCE_KILL_GRACE_SECONDS = 2.0
_TERMINATION_REQUEST_SECONDS = 2.0
_TERMINATION_BUDGET_SECONDS = 6.5
_DRAIN_GRACE_SECONDS = 2.0
_CANCELLATION_CLEANUP_SECONDS = 9.0


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


@dataclass(slots=True)
class _OutputCollector:
    max_chars: int
    chunks: list[str] = field(default_factory=list)
    retained: int = 0
    truncated: bool = False

    def append(self, text: str) -> None:
        remaining = self.max_chars - self.retained
        if len(text) > remaining:
            if remaining > 0:
                self.chunks.append(text[:remaining])
                self.retained += remaining
            self.truncated = True
            return
        self.chunks.append(text)
        self.retained += len(text)

    @property
    def text(self) -> str:
        return "".join(self.chunks)


async def _drain(
    stream: asyncio.StreamReader | None,
    collector: _OutputCollector,
) -> None:
    if stream is None:
        return
    encoding = locale.getpreferredencoding(False) if os.name == "nt" else "utf-8"
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    while data := await stream.read(64 * 1024):
        collector.append(decoder.decode(data))
    collector.append(decoder.decode(b"", final=True))


def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        creationflags = cast(int, getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with suppress(subprocess.TimeoutExpired):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                creationflags=creationflags,
                timeout=_TERMINATION_REQUEST_SECONDS,
            )
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)  # type: ignore[attr-defined]


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        with suppress(ProcessLookupError):
            process.kill()
        return
    with suppress(ProcessLookupError):
        sigkill = cast(int, getattr(signal, "SIGKILL", signal.SIGTERM))
        os.killpg(process.pid, sigkill)  # type: ignore[attr-defined]


def _close_process_transport(process: asyncio.subprocess.Process) -> None:
    transport = getattr(process, "_transport", None)
    close = getattr(transport, "close", None)
    if callable(close):
        close()


async def _wait_for_exit(
    process: asyncio.subprocess.Process,
    timeout: float,
) -> bool:
    if process.returncode is not None:
        return True
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        return False
    return True


async def _terminate(
    process: asyncio.subprocess.Process,
    *,
    include_exited_group: bool = False,
) -> None:
    async def terminate_bounded() -> None:
        if process.returncode is None or include_exited_group:
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.to_thread(_terminate_process_group, process),
                    timeout=_TERMINATION_REQUEST_SECONDS,
                )
        if await _wait_for_exit(process, _TERMINATION_GRACE_SECONDS):
            return
        _kill_process_group(process)
        await _wait_for_exit(process, _FORCE_KILL_GRACE_SECONDS)

    try:
        await asyncio.wait_for(
            terminate_bounded(),
            timeout=_TERMINATION_BUDGET_SECONDS,
        )
    except TimeoutError:
        _kill_process_group(process)


async def _finish_drains(
    tasks: tuple[asyncio.Task[None], asyncio.Task[None]],
    collectors: tuple[_OutputCollector, _OutputCollector],
) -> bool:
    done, pending = await asyncio.wait(tasks, timeout=_DRAIN_GRACE_SECONDS)
    for task, collector in zip(tasks, collectors, strict=True):
        if task not in done:
            continue
        if task.cancelled():
            collector.truncated = True
            continue
        exception = task.exception()
        if exception is not None:
            raise exception
    if not pending:
        return False
    for task, collector in zip(tasks, collectors, strict=True):
        if task in pending:
            collector.truncated = True
            task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return True


async def _cleanup_cancelled_process(
    process: asyncio.subprocess.Process | None,
    drain_tasks: tuple[asyncio.Task[None], asyncio.Task[None]] | None,
    collectors: tuple[_OutputCollector, _OutputCollector],
) -> None:
    if process is not None:
        await _terminate(process, include_exited_group=True)
    if drain_tasks is not None:
        await _finish_drains(drain_tasks, collectors)


async def _await_cleanup(cleanup: asyncio.Task[None]) -> None:
    try:
        await asyncio.wait_for(
            asyncio.shield(cleanup),
            timeout=_CANCELLATION_CLEANUP_SECONDS,
        )
    except TimeoutError:
        cleanup.cancel()
        await asyncio.gather(cleanup, return_exceptions=True)


async def _finish_cleanup_after_cancellation(cleanup: asyncio.Task[None]) -> None:
    deadline = asyncio.get_running_loop().time() + _CANCELLATION_CLEANUP_SECONDS
    while not cleanup.done():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            await asyncio.wait_for(asyncio.shield(cleanup), timeout=remaining)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    if not cleanup.done():
        cleanup.cancel()
    await asyncio.gather(cleanup, return_exceptions=True)


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
        process: asyncio.subprocess.Process | None = None
        drain_tasks: tuple[asyncio.Task[None], asyncio.Task[None]] | None = None
        collectors = (
            _OutputCollector(max_output_chars),
            _OutputCollector(max_output_chars),
        )
        timed_out = False
        exit_code = -1
        try:
            async with asyncio.timeout(timeout_seconds):
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=cwd,
                    env=environment,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **process_options,
                )
                drain_tasks = (
                    asyncio.create_task(_drain(process.stdout, collectors[0])),
                    asyncio.create_task(_drain(process.stderr, collectors[1])),
                )
                exit_code = await process.wait()
            assert process is not None
            assert drain_tasks is not None
            drain_timed_out = await _finish_drains(drain_tasks, collectors)
            if drain_timed_out:
                await _terminate(process, include_exited_group=True)
        except TimeoutError:
            timed_out = True
            cleanup = asyncio.create_task(
                _cleanup_cancelled_process(process, drain_tasks, collectors)
            )
            try:
                await _await_cleanup(cleanup)
            except asyncio.CancelledError:
                await _finish_cleanup_after_cancellation(cleanup)
                raise
            except Exception:
                cleanup.cancel()
                await asyncio.gather(cleanup, return_exceptions=True)
        except asyncio.CancelledError:
            cleanup = asyncio.create_task(
                _cleanup_cancelled_process(process, drain_tasks, collectors)
            )
            await _finish_cleanup_after_cancellation(cleanup)
            raise
        finally:
            if process is not None:
                _close_process_transport(process)

        duration_ms = (time.perf_counter() - started) * 1_000
        return ProcessResult(
            exit_code=exit_code,
            stdout=collectors[0].text,
            stderr=collectors[1].text,
            timed_out=timed_out,
            stdout_truncated=collectors[0].truncated,
            stderr_truncated=collectors[1].truncated,
            duration_ms=duration_ms,
        )
