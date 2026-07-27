from __future__ import annotations

import asyncio
import codecs
import locale
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from awesome_agent.core.tools._windows_process_job import WindowsCommandJob

_TERMINATION_GRACE_SECONDS = 2.0
_FORCE_KILL_GRACE_SECONDS = 2.0
_TERMINATION_REQUEST_SECONDS = 2.0
_TERMINATION_BUDGET_SECONDS = 6.5
_DRAIN_GRACE_SECONDS = 2.0
_DRAIN_CANCEL_GRACE_SECONDS = 0.5
_CANCELLATION_CLEANUP_SECONDS = 9.0
_PROCESS_GROUP_POLL_SECONDS = 0.02
_SUPERVISOR_EVENT_POLL_SECONDS = 0.01
_SUPERVISOR_EXIT_GRACE_SECONDS = 1.0
_SPAWN_CLEANUP_SECONDS = 0.25
_WINDOWS_STATUS_BYTES = 5
_WINDOWS_STATUS_OPEN_GRACE_SECONDS = 0.25
_WINDOWS_STATUS_CLEANUP_SECONDS = 0.25


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
                stdin=subprocess.DEVNULL,
                capture_output=True,
                creationflags=creationflags,
                timeout=_TERMINATION_REQUEST_SECONDS,
            )
        return
    with suppress(ProcessLookupError):
        kill_group = cast(Callable[[int, int], None], vars(os)["killpg"])
        kill_group(process.pid, signal.SIGTERM)


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        with suppress(ProcessLookupError):
            process.kill()
        return
    with suppress(ProcessLookupError):
        sigkill = cast(int, getattr(signal, "SIGKILL", signal.SIGTERM))
        kill_group = cast(Callable[[int, int], None], vars(os)["killpg"])
        kill_group(process.pid, sigkill)


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


async def _wait_for_returncode(process: asyncio.subprocess.Process) -> int:
    """Wait for the root process without coupling completion to pipe EOF.

    asyncio's public ``wait()`` may remain pending after the root exits while a
    descendant still holds an inherited stdout or stderr handle. Polling the
    transport-owned return code keeps root lifetime and pipe lifetime as two
    separate bounded phases.
    """

    while process.returncode is None:
        await asyncio.sleep(0.01)
    return process.returncode


async def _read_supervisor_event(fd: int, size: int) -> bytes:
    payload = bytearray()
    while len(payload) < size:
        try:
            chunk = os.read(fd, size - len(payload))
        except BlockingIOError:
            await asyncio.sleep(_SUPERVISOR_EVENT_POLL_SECONDS)
            continue
        except InterruptedError:
            continue
        if not chunk:
            raise RuntimeError("Command supervisor exited without a result.")
        payload.extend(chunk)
    return bytes(payload)


async def _wait_for_supervisor_spawn(fd: int, executable: str) -> None:
    event = await _read_supervisor_event(fd, 1)
    if event == b"S":
        return
    if event == b"E":
        error_number = int.from_bytes(
            await _read_supervisor_event(fd, 4),
            byteorder="big",
            signed=True,
        )
        raise OSError(
            error_number,
            "Unable to spawn command executable.",
            executable,
        )
    raise RuntimeError("Command supervisor returned an invalid spawn event.")


async def _wait_for_supervisor_result(fd: int) -> int:
    event = await _read_supervisor_event(fd, 1)
    if event != b"R":
        raise RuntimeError("Command supervisor returned an invalid result event.")
    return int.from_bytes(
        await _read_supervisor_event(fd, 4),
        byteorder="big",
        signed=True,
    )


def _create_windows_status_path() -> Path:
    fd, raw_path = tempfile.mkstemp(
        prefix="awesome-agent-command-",
        suffix=".status",
    )
    os.close(fd)
    path = Path(raw_path)
    path.unlink()
    return path


def _windows_status_staging_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp")


def _remove_windows_status_path(path: Path | None) -> None:
    if path is None:
        return
    for candidate in (path, _windows_status_staging_path(path)):
        with suppress(OSError):
            candidate.unlink()


async def _remove_windows_status_path_bounded(path: Path) -> bool:
    deadline = asyncio.get_running_loop().time() + _WINDOWS_STATUS_CLEANUP_SECONDS
    pending = {path, _windows_status_staging_path(path)}
    while pending:
        for candidate in tuple(pending):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pending.remove(candidate)
            except PermissionError:
                continue
            except OSError:
                return False
            else:
                pending.remove(candidate)
        if not pending:
            return True
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(_SUPERVISOR_EVENT_POLL_SECONDS, remaining))
    return True


def _read_windows_status(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            return stream.read(_WINDOWS_STATUS_BYTES + 1)
    except FileNotFoundError:
        return b""


async def _read_windows_status_bounded(path: Path) -> bytes:
    """Read one atomically published status through transient Windows locks."""

    denied: PermissionError | None = None
    deadline: float | None = None
    while True:
        try:
            payload = _read_windows_status(path)
        except PermissionError as error:
            if denied is None:
                denied = error
                deadline = (
                    asyncio.get_running_loop().time()
                    + _WINDOWS_STATUS_OPEN_GRACE_SECONDS
                )
        else:
            if denied is None or payload:
                return payload
        assert deadline is not None
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            assert denied is not None
            raise denied
        await asyncio.sleep(min(_SUPERVISOR_EVENT_POLL_SECONDS, remaining))


async def _wait_for_windows_supervisor_spawn(
    path: Path,
    process: asyncio.subprocess.Process,
    executable: str,
) -> None:
    while True:
        payload = await _read_windows_status_bounded(path)
        if len(payload) == _WINDOWS_STATUS_BYTES:
            kind = payload[:1]
            value = int.from_bytes(payload[1:], byteorder="big", signed=True)
            if kind == b"S" and value == 0:
                return
            if kind == b"E":
                error_number = value if value > 0 else 5
                raise OSError(
                    error_number,
                    "Unable to spawn command executable.",
                    executable,
                )
            raise RuntimeError("Windows command supervisor returned invalid status.")
        if len(payload) > _WINDOWS_STATUS_BYTES:
            raise RuntimeError("Windows command supervisor returned invalid status.")
        if process.returncode is not None:
            raise RuntimeError("Windows command supervisor exited before spawning.")
        await asyncio.sleep(_SUPERVISOR_EVENT_POLL_SECONDS)


def _close_fd(fd: int | None) -> None:
    if fd is None:
        return
    with suppress(OSError):
        os.close(fd)


def _posix_process_group_exists(pid: int) -> bool:
    kill_group = cast(Callable[[int, int], None], vars(os)["killpg"])
    try:
        kill_group(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_posix_process_group(pid: int, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while _posix_process_group_exists(pid):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(_PROCESS_GROUP_POLL_SECONDS, remaining))
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
        if include_exited_group and os.name != "nt":
            if await _wait_for_posix_process_group(
                process.pid,
                _TERMINATION_GRACE_SECONDS,
            ):
                return
            _kill_process_group(process)
            await _wait_for_posix_process_group(
                process.pid,
                _FORCE_KILL_GRACE_SECONDS,
            )
            return
        if include_exited_group and os.name == "nt":
            await _wait_for_exit(process, _TERMINATION_GRACE_SECONDS)
            return
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
    failure: BaseException | None = None
    for task, collector in zip(tasks, collectors, strict=True):
        if task not in done:
            continue
        if task.cancelled():
            collector.truncated = True
            continue
        exception = task.exception()
        if exception is not None and failure is None:
            failure = exception
    if not pending:
        if failure is not None:
            raise failure
        return False
    for task, collector in zip(tasks, collectors, strict=True):
        if task in pending:
            collector.truncated = True
            task.cancel()
    cancelled, stubborn = await asyncio.wait(
        pending,
        timeout=_DRAIN_CANCEL_GRACE_SECONDS,
    )
    for task in cancelled:
        _consume_task_result(task)
    for task in stubborn:
        _cancel_and_observe_task(task)
    if failure is not None:
        raise failure
    return True


async def _cleanup_cancelled_process(
    process: asyncio.subprocess.Process | None,
    spawn_task: asyncio.Task[asyncio.subprocess.Process] | None,
    windows_job: WindowsCommandJob | None,
    drain_tasks: tuple[asyncio.Task[None], asyncio.Task[None]] | None,
    collectors: tuple[_OutputCollector, _OutputCollector],
) -> None:
    recovered_process = False
    if process is None and spawn_task is not None:
        try:
            process = await asyncio.wait_for(
                asyncio.shield(spawn_task),
                timeout=_SPAWN_CLEANUP_SECONDS,
            )
            recovered_process = True
        except TimeoutError:
            _cancel_and_observe_process_task(spawn_task)
        except asyncio.CancelledError:
            _cancel_and_observe_process_task(spawn_task)
            raise
        except Exception:
            _consume_process_task_result(spawn_task)

    try:
        if windows_job is not None:
            with suppress(Exception):
                windows_job.terminate()
            windows_job.close()
            if process is not None:
                await _wait_for_exit(process, _TERMINATION_GRACE_SECONDS)
        elif process is not None:
            await _terminate(process, include_exited_group=True)
        if drain_tasks is not None:
            await _finish_drains(drain_tasks, collectors)
    finally:
        if recovered_process and process is not None:
            _close_process_transport(process)


async def _await_cleanup(cleanup: asyncio.Task[None]) -> None:
    try:
        await asyncio.wait_for(
            asyncio.shield(cleanup),
            timeout=_CANCELLATION_CLEANUP_SECONDS,
        )
    except TimeoutError:
        _cancel_and_observe_task(cleanup)


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
        _cancel_and_observe_task(cleanup)
        return
    _consume_task_result(cleanup)


def _consume_task_result(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    with suppress(BaseException):
        task.exception()


def _cancel_and_observe_task(task: asyncio.Task[None]) -> None:
    if task.done():
        _consume_task_result(task)
        return
    task.add_done_callback(_consume_task_result)
    task.cancel()


def _consume_process_task_result(
    task: asyncio.Task[asyncio.subprocess.Process],
) -> None:
    if task.cancelled():
        return
    with suppress(BaseException):
        process = task.result()
        _kill_process_group(process)
        _close_process_transport(process)


def _cancel_and_observe_process_task(
    task: asyncio.Task[asyncio.subprocess.Process],
) -> None:
    if task.done():
        _consume_process_task_result(task)
        return
    task.add_done_callback(_consume_process_task_result)
    task.cancel()


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

        started = time.perf_counter()
        process: asyncio.subprocess.Process | None = None
        spawn_task: asyncio.Task[asyncio.subprocess.Process] | None = None
        windows_job: WindowsCommandJob | None = None
        windows_job_assigned = False
        windows_status_path: Path | None = None
        lease_read_fd: int | None = None
        lease_write_fd: int | None = None
        event_read_fd: int | None = None
        event_write_fd: int | None = None
        drain_tasks: tuple[asyncio.Task[None], asyncio.Task[None]] | None = None
        collectors = (
            _OutputCollector(max_output_chars),
            _OutputCollector(max_output_chars),
        )
        timed_out = False
        exit_code = -1
        try:
            async with asyncio.timeout(timeout_seconds):
                spawn_argv = argv
                if os.name == "nt":
                    windows_job = WindowsCommandJob.create()
                    windows_status_path = _create_windows_status_path()
                    supervisor = Path(__file__).with_name(
                        "_windows_process_supervisor.py"
                    )
                    spawn_argv = [
                        sys.executable,
                        str(supervisor),
                        windows_job.event_name,
                        str(windows_status_path),
                        "--",
                        *argv,
                    ]
                else:
                    lease_read_fd, lease_write_fd = os.pipe()
                    event_read_fd, event_write_fd = os.pipe()
                    os.set_blocking(event_read_fd, False)
                    process_options["start_new_session"] = True
                    process_options["close_fds"] = True
                    process_options["pass_fds"] = (
                        lease_read_fd,
                        event_write_fd,
                    )
                    supervisor = Path(__file__).with_name("_process_supervisor.py")
                    spawn_argv = [
                        sys.executable,
                        str(supervisor),
                        str(lease_read_fd),
                        str(event_write_fd),
                        "--",
                        *argv,
                    ]
                spawn_task = asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        *spawn_argv,
                        cwd=cwd,
                        env=environment,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        **process_options,
                    )
                )
                process = await asyncio.shield(spawn_task)
                _close_fd(lease_read_fd)
                lease_read_fd = None
                _close_fd(event_write_fd)
                event_write_fd = None
                drain_tasks = (
                    asyncio.create_task(_drain(process.stdout, collectors[0])),
                    asyncio.create_task(_drain(process.stderr, collectors[1])),
                )
                if windows_job is not None:
                    assert windows_status_path is not None
                    windows_job.assign(process.pid)
                    windows_job_assigned = True
                    windows_job.release_supervisor()
                    try:
                        await _wait_for_windows_supervisor_spawn(
                            windows_status_path,
                            process,
                            argv[0],
                        )
                    finally:
                        if await _remove_windows_status_path_bounded(
                            windows_status_path
                        ):
                            windows_status_path = None
                    exit_code = await _wait_for_returncode(process)
                    # The target root is complete. End every process that
                    # inherited this command's job before waiting for pipe EOF.
                    windows_job.terminate()
                else:
                    assert event_read_fd is not None
                    await _wait_for_supervisor_spawn(event_read_fd, argv[0])
                    exit_code = await _wait_for_supervisor_result(event_read_fd)
            assert process is not None
            assert drain_tasks is not None
            drain_timed_out = await _finish_drains(drain_tasks, collectors)
            if drain_timed_out and windows_job_assigned:
                assert windows_job is not None
                windows_job.terminate()
            elif drain_timed_out or (
                event_read_fd is not None
                and not await _wait_for_exit(
                    process,
                    _SUPERVISOR_EXIT_GRACE_SECONDS,
                )
            ):
                await _terminate(process, include_exited_group=True)
        except TimeoutError:
            timed_out = True
            cleanup = asyncio.create_task(
                _cleanup_cancelled_process(
                    process,
                    spawn_task,
                    windows_job if windows_job_assigned else None,
                    drain_tasks,
                    collectors,
                )
            )
            try:
                await _await_cleanup(cleanup)
            except asyncio.CancelledError:
                await _finish_cleanup_after_cancellation(cleanup)
                raise
            except Exception:
                _cancel_and_observe_task(cleanup)
        except asyncio.CancelledError:
            cleanup = asyncio.create_task(
                _cleanup_cancelled_process(
                    process,
                    spawn_task,
                    windows_job if windows_job_assigned else None,
                    drain_tasks,
                    collectors,
                )
            )
            await _finish_cleanup_after_cancellation(cleanup)
            raise
        except Exception:
            cleanup = asyncio.create_task(
                _cleanup_cancelled_process(
                    process,
                    spawn_task,
                    windows_job if windows_job_assigned else None,
                    drain_tasks,
                    collectors,
                )
            )
            try:
                await _await_cleanup(cleanup)
            except asyncio.CancelledError:
                await _finish_cleanup_after_cancellation(cleanup)
                raise
            except Exception:
                _cancel_and_observe_task(cleanup)
            raise
        finally:
            _close_fd(lease_read_fd)
            _close_fd(lease_write_fd)
            _close_fd(event_read_fd)
            _close_fd(event_write_fd)
            _remove_windows_status_path(windows_status_path)
            if windows_job is not None:
                windows_job.close()
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
