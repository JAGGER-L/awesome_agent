import asyncio
import ctypes
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from textwrap import dedent
from time import monotonic
from typing import Any, NamedTuple, cast

import pytest

import awesome_agent.core.tools.process as process_module
from awesome_agent.core.tools._windows_process_job import WindowsCommandJob
from awesome_agent.core.tools.process import ProcessRunner

_SIGKILL = cast(int, vars(signal).get("SIGKILL", signal.SIGTERM))
_PROC_SELF_STAT = Path("/proc/self/stat")
_TERMINAL_PROC_STATES = frozenset({"Z", "X", "x"})


class _ProcessStat(NamedTuple):
    state: str
    ppid: int
    pgid: int
    session: int
    starttime: int


class _PinnedWindowsProcess(NamedTuple):
    pid: int
    kernel32: Any
    handle: int


def _windows_api_assertion(operation: str, *, pid: int) -> AssertionError:
    get_last_error = cast(
        "Callable[[], int] | None",
        getattr(ctypes, "get_last_error", None),
    )
    error_code = int(get_last_error()) if get_last_error is not None else 0
    format_error = cast(
        "Callable[[int], str] | None",
        getattr(ctypes, "FormatError", None),
    )
    detail = (
        str(format_error(error_code)).strip()
        if format_error is not None and error_code
        else "Win32 diagnostics unavailable"
    )
    return AssertionError(
        f"{operation} failed for pid={pid} (WinError {error_code}: {detail})."
    )


def _pin_windows_process(pid: int) -> _PinnedWindowsProcess | None:
    if os.name != "nt":
        return None
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise AssertionError("Win32 process APIs are unavailable.")
    kernel32: Any = win_dll("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    wait_for_single_object.restype = ctypes.c_uint32
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    handle = open_process(0x00100000, 0, pid)
    if not handle:
        raise _windows_api_assertion("OpenProcess", pid=pid)
    return _PinnedWindowsProcess(pid=pid, kernel32=kernel32, handle=int(handle))


def _wait_for_pinned_windows_process_stop(
    process: _PinnedWindowsProcess,
    *,
    timeout: float = 5.0,
) -> None:
    result = int(
        process.kernel32.WaitForSingleObject(
            process.handle,
            int(timeout * 1_000),
        )
    )
    if result == 0x00000000:
        return
    if result == 0x00000102:
        raise AssertionError(f"process remained alive: pid={process.pid}")
    if result == 0xFFFFFFFF:
        raise _windows_api_assertion("WaitForSingleObject", pid=process.pid)
    raise AssertionError(
        f"WaitForSingleObject returned {result:#x} for pid={process.pid}."
    )


def _assert_pinned_windows_process_running(process: _PinnedWindowsProcess) -> None:
    result = int(process.kernel32.WaitForSingleObject(process.handle, 0))
    if result == 0x00000102:
        return
    if result == 0x00000000:
        raise AssertionError(f"process exited before timeout: pid={process.pid}")
    if result == 0xFFFFFFFF:
        raise _windows_api_assertion("WaitForSingleObject", pid=process.pid)
    raise AssertionError(
        f"WaitForSingleObject returned {result:#x} for pid={process.pid}."
    )


def _close_pinned_windows_process(process: _PinnedWindowsProcess | None) -> None:
    if process is not None:
        process.kernel32.CloseHandle(process.handle)


def _posix_process_group(pid: int) -> int:
    get_process_group = cast(Callable[[int], int], vars(os)["getpgid"])
    return get_process_group(pid)


def _kill_posix_process_group(pid: int) -> None:
    kill_process_group = cast(Callable[[int, int], None], vars(os)["killpg"])
    kill_process_group(pid, _SIGKILL)


def _posix_process_session(pid: int) -> int:
    get_process_session = cast(Callable[[int], int], vars(os)["getsid"])
    return get_process_session(pid)


def _read_process_stat(pid: int) -> _ProcessStat | None:
    try:
        raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return None
    fields = raw[raw.rfind(")") + 1 :].split()
    return _ProcessStat(
        state=fields[0][:1],
        ppid=int(fields[1]),
        pgid=int(fields[2]),
        session=int(fields[3]),
        starttime=int(fields[19]),
    )


def _process_details(pid: int) -> str:
    current = _read_process_stat(pid)
    if current is not None:
        return f"pid={pid} stat={current!r}"
    return (
        f"pid={pid} state=unknown ppid=unknown pgid=unknown "
        "session=unknown starttime=unknown"
    )


def _process_is_running(pid: int, *, starttime: int | None = None) -> bool:
    if os.name == "nt":
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            raise AssertionError("Win32 process APIs are unavailable.")
        kernel32 = win_dll("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        open_process.restype = ctypes.c_void_p
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        wait_for_single_object.restype = ctypes.c_uint32
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        handle = open_process(0x00100000 | 0x1000, 0, pid)
        if not handle:
            return False
        try:
            return int(wait_for_single_object(handle, 0)) == 0x00000102
        finally:
            close_handle(handle)
    if _PROC_SELF_STAT.is_file():
        current = _read_process_stat(pid)
        return (
            current is not None
            and current.state not in _TERMINAL_PROC_STATES
            and (starttime is None or current.starttime == starttime)
        )
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_pid_file(path: Path, *, timeout: float = 5.0) -> int:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        try:
            pid = int(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            pass
        else:
            if pid > 0:
                return pid
        import time

        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for pid file: {path}")


def _wait_for_process_stop(
    pid: int,
    *,
    timeout: float = 5.0,
    starttime: int | None = None,
) -> None:
    deadline = monotonic() + timeout
    while _process_is_running(pid, starttime=starttime) and monotonic() < deadline:
        import time

        time.sleep(0.02)
    assert not _process_is_running(pid, starttime=starttime), (
        f"process remained alive: {_process_details(pid)}; "
        f"expected_starttime={starttime}"
    )


def _use_fake_procfs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "self.stat"
    marker.write_text("available", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_PROC_SELF_STAT", marker)


def test_read_process_stat_only_treats_a_missing_process_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*args: object, **kwargs: object) -> str:
        raise FileNotFoundError

    def vanished(*args: object, **kwargs: object) -> str:
        raise ProcessLookupError

    def denied(*args: object, **kwargs: object) -> str:
        raise PermissionError("stat denied")

    def malformed(*args: object, **kwargs: object) -> str:
        return "malformed"

    monkeypatch.setattr(Path, "read_text", missing)
    assert _read_process_stat(42) is None

    monkeypatch.setattr(Path, "read_text", vanished)
    assert _read_process_stat(42) is None

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(PermissionError, match="stat denied"):
        _read_process_stat(42)

    monkeypatch.setattr(Path, "read_text", malformed)
    with pytest.raises(IndexError):
        _read_process_stat(42)


def test_pinned_process_identity_is_absent_when_proc_stat_disappears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        return
    _use_fake_procfs(tmp_path, monkeypatch)
    monkeypatch.setattr(sys.modules[__name__], "_read_process_stat", lambda pid: None)

    assert _process_is_running(42, starttime=100) is False


@pytest.mark.parametrize(
    ("state", "observed_starttime", "expected"),
    [
        ("R", 100, True),
        ("S", 101, False),
        ("Z", 100, False),
        ("X", 100, False),
        ("x", 100, False),
    ],
)
def test_pinned_process_identity_requires_the_same_live_proc_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    observed_starttime: int,
    expected: bool,
) -> None:
    if os.name == "nt":
        return
    _use_fake_procfs(tmp_path, monkeypatch)
    snapshot = _ProcessStat(state, 1, 42, 42, observed_starttime)
    monkeypatch.setattr(
        sys.modules[__name__],
        "_read_process_stat",
        lambda pid: snapshot,
    )

    assert _process_is_running(42, starttime=100) is expected


def test_pinned_process_identity_does_not_hide_proc_permission_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        return
    _use_fake_procfs(tmp_path, monkeypatch)

    def denied(pid: int) -> _ProcessStat | None:
        raise PermissionError("stat denied")

    monkeypatch.setattr(sys.modules[__name__], "_read_process_stat", denied)

    with pytest.raises(PermissionError, match="stat denied"):
        _process_is_running(42, starttime=100)


@pytest.mark.asyncio
async def test_process_runner_completes_and_bounds_output(tmp_path: Path) -> None:
    result = await ProcessRunner().run(
        argv=[sys.executable, "-c", "print('x' * 100)"],
        cwd=tmp_path,
        environment=dict(os.environ),
        timeout_seconds=5,
        max_output_chars=10,
    )

    assert result.exit_code == 0
    assert result.stdout == "x" * 10
    assert result.stdout_truncated is True
    assert result.stderr == ""
    assert result.timed_out is False
    assert result.duration_ms >= 0


def test_process_runner_does_not_inherit_core_stdin(tmp_path: Path) -> None:
    runner_source = dedent(
        f"""
        import asyncio
        import os
        import sys
        from pathlib import Path
        from awesome_agent.core.tools.process import ProcessRunner

        async def main():
            result = await ProcessRunner().run(
                argv=[
                    sys.executable,
                    "-c",
                    "import sys; print(len(sys.stdin.buffer.read()))",
                ],
                cwd=Path({str(tmp_path)!r}),
                environment=dict(os.environ),
                timeout_seconds=2.0,
                max_output_chars=1_000,
            )
            print(
                f"{{int(result.timed_out)}}|{{result.exit_code}}|"
                f"{{result.stdout.strip()}}",
                flush=True,
            )

        asyncio.run(main())
        """
    )
    core = subprocess.Popen(
        [sys.executable, "-c", runner_source],
        cwd=tmp_path,
        env=dict(os.environ),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        core.wait(timeout=10)
        assert core.stdout is not None
        assert core.stderr is not None
        output = core.stdout.read()
        errors = core.stderr.read()
    finally:
        if core.stdin is not None:
            core.stdin.close()
        if core.poll() is None:
            core.kill()
            core.wait(timeout=10)

    assert core.returncode == 0, errors
    assert output == "0|0|0\n"


@pytest.mark.asyncio
async def test_process_runner_times_out_and_terminates(tmp_path: Path) -> None:
    result = await ProcessRunner().run(
        argv=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        environment=dict(os.environ),
        timeout_seconds=0.1,
        max_output_chars=1_000,
    )

    assert result.exit_code == -1
    assert result.timed_out is True


@pytest.mark.asyncio
async def test_process_runner_timeout_reaps_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeout_scope: asyncio.Timeout | None = None
    real_timeout = asyncio.timeout

    def deferred_timeout(_: float) -> asyncio.Timeout:
        nonlocal timeout_scope
        assert timeout_scope is None
        timeout_scope = real_timeout(None)
        return timeout_scope

    monkeypatch.setattr(asyncio, "timeout", deferred_timeout)
    descendant_pid_file = tmp_path / "timeout-descendant.pid"
    descendant_source = "import time; time.sleep(30)"
    parent_source = dedent(
        f"""
        import subprocess
        import sys
        import time
        from pathlib import Path

        child = subprocess.Popen(
            [sys.executable, "-c", {descendant_source!r}],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        Path({str(descendant_pid_file)!r}).write_text(
            str(child.pid),
            encoding="utf-8",
        )
        time.sleep(30)
        """
    )

    task = asyncio.create_task(
        ProcessRunner().run(
            argv=[sys.executable, "-c", parent_source],
            cwd=tmp_path,
            environment=dict(os.environ),
            timeout_seconds=60,
            max_output_chars=1_000,
        )
    )
    pinned_windows_process: _PinnedWindowsProcess | None = None
    try:
        descendant_pid = await asyncio.to_thread(
            _wait_for_pid_file,
            descendant_pid_file,
        )
        pinned_windows_process = _pin_windows_process(descendant_pid)
        descendant_starttime = None
        if pinned_windows_process is None and _PROC_SELF_STAT.is_file():
            descendant_stat = _read_process_stat(descendant_pid)
            assert descendant_stat is not None
            descendant_starttime = descendant_stat.starttime
        if pinned_windows_process is not None:
            _assert_pinned_windows_process_running(pinned_windows_process)
        else:
            assert _process_is_running(
                descendant_pid,
                starttime=descendant_starttime,
            )
        assert timeout_scope is not None
        timeout_scope.reschedule(asyncio.get_running_loop().time())
        result = await asyncio.wait_for(task, timeout=15)
        if pinned_windows_process is not None:
            _wait_for_pinned_windows_process_stop(pinned_windows_process)
        else:
            _wait_for_process_stop(
                descendant_pid,
                starttime=descendant_starttime,
            )
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        _close_pinned_windows_process(pinned_windows_process)

    assert result.exit_code == -1
    assert result.timed_out is True


@pytest.mark.asyncio
async def test_process_runner_cancellation_terminates_quickly(tmp_path: Path) -> None:
    task = asyncio.create_task(
        ProcessRunner().run(
            argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            environment=dict(os.environ),
            timeout_seconds=60,
            max_output_chars=1_000,
        )
    )
    await asyncio.sleep(0.1)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_process_runner_cancellation_reaps_descendant(tmp_path: Path) -> None:
    descendant_pid_file = tmp_path / "cancel-descendant.pid"
    descendant_source = "import time; time.sleep(30)"
    parent_source = dedent(
        f"""
        import subprocess
        import sys
        import time
        from pathlib import Path

        child = subprocess.Popen(
            [sys.executable, "-c", {descendant_source!r}],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        Path({str(descendant_pid_file)!r}).write_text(
            str(child.pid),
            encoding="utf-8",
        )
        time.sleep(30)
        """
    )
    task = asyncio.create_task(
        ProcessRunner().run(
            argv=[sys.executable, "-c", parent_source],
            cwd=tmp_path,
            environment=dict(os.environ),
            timeout_seconds=60,
            max_output_chars=1_000,
        )
    )
    descendant_pid = await asyncio.to_thread(
        _wait_for_pid_file,
        descendant_pid_file,
    )

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    _wait_for_process_stop(descendant_pid)


@pytest.mark.asyncio
async def test_process_runner_bounds_subprocess_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_spawns(*args: object, **kwargs: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", never_spawns)

    started = monotonic()
    result = await ProcessRunner().run(
        argv=[sys.executable, "-c", "print('unreachable')"],
        cwd=tmp_path,
        environment=dict(os.environ),
        timeout_seconds=0.01,
        max_output_chars=1_000,
    )

    assert result.timed_out is True
    assert result.exit_code == -1
    assert monotonic() - started < 1


def test_windows_tree_termination_never_inherits_application_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class WindowsOs:
        name = "nt"

    def run_taskkill(*args: object, **kwargs: object) -> object:
        del args
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            args=["taskkill"],
            returncode=0,
            stdout=b"",
            stderr=b"",
        )

    monkeypatch.setattr(process_module, "os", WindowsOs())
    monkeypatch.setattr(subprocess, "run", run_taskkill)
    process = cast(asyncio.subprocess.Process, type("Process", (), {"pid": 42})())

    process_module._terminate_process_group(process)

    assert observed["stdin"] is subprocess.DEVNULL


@pytest.mark.asyncio
async def test_process_runner_preserves_executable_spawn_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing-awesome-executable"
    status_path = tmp_path / "spawn-error.status"

    def create_status_path() -> Path:
        return status_path

    if os.name == "nt":
        monkeypatch.setattr(
            process_module,
            "_create_windows_status_path",
            create_status_path,
        )

    with pytest.raises(FileNotFoundError) as error:
        await ProcessRunner().run(
            argv=[str(missing), "literal argument"],
            cwd=tmp_path,
            environment=dict(os.environ),
            timeout_seconds=2,
            max_output_chars=1_000,
        )
    assert error.value.filename == str(missing)
    assert not status_path.exists()
    assert not process_module._windows_status_staging_path(status_path).exists()


@pytest.mark.asyncio
async def test_windows_spawn_status_retries_a_transient_open_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"S" + (0).to_bytes(4, byteorder="big", signed=True)
    attempts = 0

    def read_status(path: Path) -> bytes:
        nonlocal attempts
        assert path == tmp_path / "spawn.status"
        attempts += 1
        if attempts == 1:
            raise PermissionError(13, "status publication is still locked", path)
        return payload

    process = cast(
        asyncio.subprocess.Process,
        type("ExitedSupervisor", (), {"returncode": 0})(),
    )
    monkeypatch.setattr(process_module, "_read_windows_status", read_status)

    await process_module._wait_for_windows_supervisor_spawn(
        tmp_path / "spawn.status",
        process,
        sys.executable,
    )

    assert attempts == 2


@pytest.mark.asyncio
async def test_windows_spawn_status_preserves_a_persistent_open_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_path = tmp_path / "spawn.status"

    def read_status(path: Path) -> bytes:
        assert path == status_path
        raise PermissionError(13, "status publication remains locked", path)

    process = cast(
        asyncio.subprocess.Process,
        type("RunningSupervisor", (), {"returncode": None})(),
    )
    monkeypatch.setattr(process_module, "_read_windows_status", read_status)
    monkeypatch.setattr(process_module, "_WINDOWS_STATUS_OPEN_GRACE_SECONDS", 0)

    with pytest.raises(PermissionError) as error:
        await process_module._wait_for_windows_supervisor_spawn(
            status_path,
            process,
            sys.executable,
        )

    assert error.value.filename == status_path


@pytest.mark.asyncio
async def test_windows_spawn_status_keeps_denial_when_publication_disappears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_path = tmp_path / "spawn.status"
    attempts = 0

    def read_status(path: Path) -> bytes:
        nonlocal attempts
        assert path == status_path
        attempts += 1
        if attempts == 1:
            raise PermissionError(13, "status publication was locked", path)
        return b""

    process = cast(
        asyncio.subprocess.Process,
        type("ExitedSupervisor", (), {"returncode": 0})(),
    )
    monkeypatch.setattr(process_module, "_read_windows_status", read_status)
    monkeypatch.setattr(process_module, "_WINDOWS_STATUS_OPEN_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(process_module, "_SUPERVISOR_EVENT_POLL_SECONDS", 0)

    with pytest.raises(PermissionError) as error:
        await process_module._wait_for_windows_supervisor_spawn(
            status_path,
            process,
            sys.executable,
        )

    assert attempts > 1
    assert error.value.filename == status_path


@pytest.mark.asyncio
async def test_windows_spawn_status_reports_exit_without_an_open_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_path = tmp_path / "spawn.status"
    process = cast(
        asyncio.subprocess.Process,
        type("ExitedSupervisor", (), {"returncode": 0})(),
    )
    monkeypatch.setattr(process_module, "_read_windows_status", lambda path: b"")

    with pytest.raises(
        RuntimeError,
        match="Windows command supervisor exited before spawning",
    ):
        await process_module._wait_for_windows_supervisor_spawn(
            status_path,
            process,
            sys.executable,
        )


@pytest.mark.asyncio
async def test_process_runner_bounds_pipe_drain_from_inherited_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_module, "_DRAIN_GRACE_SECONDS", 0.05)

    async def inherited_pipe_never_closes(*args: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(process_module, "_drain", inherited_pipe_never_closes)

    started = monotonic()
    result = await asyncio.wait_for(
        ProcessRunner().run(
            argv=[sys.executable, "-c", "print('parent done')"],
            cwd=tmp_path,
            environment=dict(os.environ),
            timeout_seconds=2,
            max_output_chars=1_000,
        ),
        timeout=1,
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.stdout_truncated is True
    assert monotonic() - started < 1


def test_process_runner_closes_real_inherited_pipe_transports(
    tmp_path: Path,
) -> None:
    grandchild_source = (
        "import os,signal,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN) if os.name!='nt' else None;"
        "time.sleep(5)"
    )
    parent_source = (
        "import subprocess,sys;"
        "child=subprocess.Popen("
        f"[sys.executable,'-c',{grandchild_source!r}],"
        "stdout=sys.stdout,stderr=sys.stderr);"
        "print(child.pid,flush=True)"
    )
    runner_source = dedent(
        f"""
        import asyncio
        import os
        import sys
        import time
        from pathlib import Path
        from awesome_agent.core.tools.process import ProcessRunner

        def is_alive(pid):
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            status = Path('/proc') / str(pid) / 'stat'
            if status.exists():
                try:
                    return status.read_text(encoding='utf-8').split()[2] != 'Z'
                except OSError:
                    return True
            return True

        async def main():
            result = await ProcessRunner().run(
                argv=[sys.executable, '-c', {parent_source!r}],
                cwd=Path.cwd(),
                environment=dict(os.environ),
                timeout_seconds=2,
                max_output_chars=1000,
            )
            child_pid = int(result.stdout.strip().splitlines()[0])
            deadline = time.monotonic() + 1
            while is_alive(child_pid) and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert not is_alive(child_pid), child_pid
            assert result.exit_code == 0, result
            assert result.timed_out is False, result
            assert result.stdout_truncated is False, result
            current = asyncio.current_task()
            pending = [
                task
                for task in asyncio.all_tasks()
                if task is not current and not task.done()
            ]
            assert pending == [], [
                (task.get_name(), repr(task.get_coro())) for task in pending
            ]
            print('runner-complete', flush=True)

        asyncio.run(main())
        """
    )
    environment = dict(os.environ)
    environment["PYTHONWARNINGS"] = "always::ResourceWarning"

    started = monotonic()
    completed = subprocess.run(
        [sys.executable, "-c", runner_source],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert "runner-complete" in completed.stdout
    assert "unclosed transport" not in completed.stderr.casefold()
    assert "exception ignored" not in completed.stderr.casefold()
    assert monotonic() - started < 10


@pytest.mark.asyncio
async def test_process_runner_reaps_descendant_that_closes_output(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        return

    descendant_source = "import time; time.sleep(60)"
    parent_source = (
        "import subprocess,sys;"
        "child=subprocess.Popen("
        f"[sys.executable,'-c',{descendant_source!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL);"
        "print(child.pid,flush=True)"
    )

    result = await ProcessRunner().run(
        argv=[sys.executable, "-c", parent_source],
        cwd=tmp_path,
        environment=dict(os.environ),
        timeout_seconds=5,
        max_output_chars=1_000,
    )

    descendant_pid = int(result.stdout.strip())
    _wait_for_process_stop(descendant_pid)
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.stdout_truncated is False
    assert result.stderr_truncated is False


@pytest.mark.asyncio
async def test_windows_runner_reaps_descendant_after_root_exits(
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        return

    descendant_source = "import time; time.sleep(30)"
    parent_source = (
        "import subprocess,sys;"
        "child=subprocess.Popen("
        f"[sys.executable,'-c',{descendant_source!r}],"
        "stdin=subprocess.DEVNULL,stdout=sys.stdout,stderr=sys.stderr);"
        "print(child.pid,flush=True)"
    )
    descendant_pid: int | None = None
    try:
        result = await ProcessRunner().run(
            argv=[sys.executable, "-c", parent_source],
            cwd=tmp_path,
            environment=dict(os.environ),
            timeout_seconds=10,
            max_output_chars=1_000,
        )
        descendant_pid = int(result.stdout.strip())

        assert result.exit_code == 0
        assert result.timed_out is False
        _wait_for_process_stop(descendant_pid, timeout=1)
    finally:
        if descendant_pid is not None and _process_is_running(descendant_pid):
            subprocess.run(
                ["taskkill", "/PID", str(descendant_pid), "/T", "/F"],
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=5,
            )


@pytest.mark.asyncio
async def test_windows_runner_assigns_job_before_releasing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt":
        return

    marker = tmp_path / "target-started.txt"
    status_path = tmp_path / "successful-spawn.status"
    original_assign = WindowsCommandJob.assign

    def assign_after_observation(job: WindowsCommandJob, pid: int) -> None:
        assert not marker.exists()
        original_assign(job, pid)
        assert not marker.exists()

    def create_status_path() -> Path:
        return status_path

    monkeypatch.setattr(WindowsCommandJob, "assign", assign_after_observation)
    monkeypatch.setattr(
        process_module,
        "_create_windows_status_path",
        create_status_path,
    )

    result = await ProcessRunner().run(
        argv=[
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).touch()",
        ],
        cwd=tmp_path,
        environment=dict(os.environ),
        timeout_seconds=5,
        max_output_chars=1_000,
    )

    assert result.exit_code == 0
    assert marker.exists()
    assert not status_path.exists()
    assert not process_module._windows_status_staging_path(status_path).exists()


def test_windows_command_job_nests_under_core_lifetime_job(tmp_path: Path) -> None:
    if os.name != "nt":
        return

    runner_source = dedent(
        """
        import asyncio
        import os
        import sys
        from pathlib import Path
        from awesome_agent.core.process_lifetime import install_process_tree_guard
        from awesome_agent.core.tools.process import ProcessRunner

        async def main():
            install_process_tree_guard()
            result = await ProcessRunner().run(
                argv=[sys.executable, "-c", "print('nested-job-ok')"],
                cwd=Path.cwd(),
                environment=dict(os.environ),
                timeout_seconds=5,
                max_output_chars=1_000,
            )
            assert result.exit_code == 0, result
            assert result.stdout.strip() == "nested-job-ok", result

        asyncio.run(main())
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", runner_source],
        cwd=tmp_path,
        env=dict(os.environ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.asyncio
async def test_process_runner_preserves_timeout_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_spawns(*args: object, **kwargs: object) -> None:
        await asyncio.Event().wait()

    async def cleanup_fails(*args: object, **kwargs: object) -> None:
        raise RuntimeError("cleanup backend failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", never_spawns)
    monkeypatch.setattr(
        process_module,
        "_cleanup_cancelled_process",
        cleanup_fails,
    )

    result = await ProcessRunner().run(
        argv=[sys.executable, "-c", "print('unreachable')"],
        cwd=tmp_path,
        environment=dict(os.environ),
        timeout_seconds=0.01,
        max_output_chars=1_000,
    )

    assert result.timed_out is True
    assert result.exit_code == -1


@pytest.mark.asyncio
async def test_process_runner_preserves_cancellation_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn_started = asyncio.Event()

    async def never_spawns(*args: object, **kwargs: object) -> None:
        spawn_started.set()
        await asyncio.Event().wait()

    async def cleanup_fails(*args: object, **kwargs: object) -> None:
        raise RuntimeError("cleanup backend failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", never_spawns)
    monkeypatch.setattr(
        process_module,
        "_cleanup_cancelled_process",
        cleanup_fails,
    )
    task = asyncio.create_task(
        ProcessRunner().run(
            argv=[sys.executable, "-c", "print('unreachable')"],
            cwd=tmp_path,
            environment=dict(os.environ),
            timeout_seconds=60,
            max_output_chars=1_000,
        )
    )
    await spawn_started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_process_runner_repeated_cancellation_does_not_abort_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_completed = asyncio.Event()

    async def never_spawns(*args: object, **kwargs: object) -> None:
        spawn_started.set()
        await asyncio.Event().wait()

    async def cleanup_finishes(*args: object, **kwargs: object) -> None:
        cleanup_started.set()
        await asyncio.sleep(0.05)
        cleanup_completed.set()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", never_spawns)
    monkeypatch.setattr(
        process_module,
        "_cleanup_cancelled_process",
        cleanup_finishes,
    )
    task = asyncio.create_task(
        ProcessRunner().run(
            argv=[sys.executable, "-c", "print('unreachable')"],
            cwd=tmp_path,
            environment=dict(os.environ),
            timeout_seconds=60,
            max_output_chars=1_000,
        )
    )
    await spawn_started.wait()

    task.cancel()
    await cleanup_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleanup_completed.is_set()


@pytest.mark.asyncio
async def test_process_runner_bounds_cleanup_that_swallows_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_cancelled = asyncio.Event()
    release_cleanup = asyncio.Event()
    observed: list[BaseException | None] = []

    async def never_spawns(*args: object, **kwargs: object) -> None:
        spawn_started.set()
        await asyncio.Event().wait()

    async def cancellation_resistant_cleanup(
        *args: object,
        **kwargs: object,
    ) -> None:
        cleanup_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_cancelled.set()
            await release_cleanup.wait()
        raise RuntimeError("late cleanup failure")

    def observe_cleanup(task: asyncio.Task[None]) -> None:
        observed.append(task.exception())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", never_spawns)
    monkeypatch.setattr(
        process_module,
        "_cleanup_cancelled_process",
        cancellation_resistant_cleanup,
    )
    monkeypatch.setattr(process_module, "_CANCELLATION_CLEANUP_SECONDS", 0.05)
    monkeypatch.setattr(process_module, "_consume_task_result", observe_cleanup)
    task = asyncio.create_task(
        ProcessRunner().run(
            argv=[sys.executable, "-c", "print('unreachable')"],
            cwd=tmp_path,
            environment=dict(os.environ),
            timeout_seconds=60,
            max_output_chars=1_000,
        )
    )
    await spawn_started.wait()

    started = monotonic()
    task.cancel()
    await cleanup_started.wait()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.5)
    assert monotonic() - started < 0.5
    await asyncio.wait_for(cleanup_cancelled.wait(), timeout=0.5)

    release_cleanup.set()
    deadline = asyncio.get_running_loop().time() + 0.5
    while not observed and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert len(observed) == 1
    assert isinstance(observed[0], RuntimeError)


@pytest.mark.asyncio
async def test_process_runner_bounds_timeout_cleanup_that_swallows_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_cancelled = asyncio.Event()
    release_cleanup = asyncio.Event()
    observed: list[BaseException | None] = []

    async def never_spawns(*args: object, **kwargs: object) -> None:
        await asyncio.Event().wait()

    async def cancellation_resistant_cleanup(
        *args: object,
        **kwargs: object,
    ) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_cancelled.set()
            await release_cleanup.wait()
        raise RuntimeError("late timeout cleanup failure")

    def observe_cleanup(task: asyncio.Task[None]) -> None:
        observed.append(task.exception())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", never_spawns)
    monkeypatch.setattr(
        process_module,
        "_cleanup_cancelled_process",
        cancellation_resistant_cleanup,
    )
    monkeypatch.setattr(process_module, "_CANCELLATION_CLEANUP_SECONDS", 0.05)
    monkeypatch.setattr(process_module, "_consume_task_result", observe_cleanup)

    started = monotonic()
    result = await asyncio.wait_for(
        ProcessRunner().run(
            argv=[sys.executable, "-c", "print('unreachable')"],
            cwd=tmp_path,
            environment=dict(os.environ),
            timeout_seconds=0.01,
            max_output_chars=1_000,
        ),
        timeout=0.5,
    )

    assert result.timed_out is True
    assert monotonic() - started < 0.5
    await asyncio.wait_for(cleanup_cancelled.wait(), timeout=0.5)

    release_cleanup.set()
    deadline = asyncio.get_running_loop().time() + 0.5
    while not observed and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert len(observed) == 1
    assert isinstance(observed[0], RuntimeError)


def test_process_runner_kills_command_tree_when_core_is_sigkilled(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        return

    command_pid_file = tmp_path / "command.pid"
    descendant_pid_file = tmp_path / "descendant.pid"
    descendant_source = "import time; time.sleep(60)"
    command_source = dedent(
        f"""
        import os
        import subprocess
        import sys
        import time
        from pathlib import Path

        child = subprocess.Popen([sys.executable, "-c", {descendant_source!r}])
        Path({str(command_pid_file)!r}).write_text(str(os.getpid()), encoding="utf-8")
        Path({str(descendant_pid_file)!r}).write_text(str(child.pid), encoding="utf-8")
        time.sleep(60)
        """
    )
    core_source = dedent(
        f"""
        import asyncio
        import os
        import sys
        from pathlib import Path
        from awesome_agent.core.tools.process import ProcessRunner

        asyncio.run(ProcessRunner().run(
            argv=[sys.executable, "-c", {command_source!r}],
            cwd=Path({str(tmp_path)!r}),
            environment=dict(os.environ),
            timeout_seconds=60,
            max_output_chars=1_000,
        ))
        """
    )
    core = subprocess.Popen(
        [sys.executable, "-c", core_source],
        cwd=tmp_path,
        env=dict(os.environ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    command_pid: int | None = None
    descendant_pid: int | None = None
    command_group: int | None = None
    command_starttime: int | None = None
    descendant_starttime: int | None = None
    try:
        command_pid = _wait_for_pid_file(command_pid_file)
        descendant_pid = _wait_for_pid_file(descendant_pid_file)
        command_group = _posix_process_group(command_pid)
        descendant_group = _posix_process_group(descendant_pid)
        command_session = _posix_process_session(command_pid)
        descendant_session = _posix_process_session(descendant_pid)
        command_stat = _read_process_stat(command_pid)
        descendant_stat = _read_process_stat(descendant_pid)
        command_starttime = command_stat.starttime if command_stat else None
        descendant_starttime = descendant_stat.starttime if descendant_stat else None
        topology_ready = (
            command_group == descendant_group
            and command_group == command_session
            and command_session == descendant_session
            and _process_is_running(command_pid, starttime=command_starttime)
            and _process_is_running(descendant_pid, starttime=descendant_starttime)
            and (
                not _PROC_SELF_STAT.is_file()
                or (command_stat is not None and descendant_stat is not None)
            )
        )
        assert topology_ready, (
            "Command tree was not ready in one POSIX cleanup domain: "
            f"command=({_process_details(command_pid)}); "
            f"descendant=({_process_details(descendant_pid)})"
        )

        os.kill(core.pid, _SIGKILL)
        core.wait(timeout=5)

        _wait_for_process_stop(
            command_pid,
            starttime=command_starttime,
        )
        _wait_for_process_stop(
            descendant_pid,
            starttime=descendant_starttime,
        )
    finally:
        if core.poll() is None:
            core.kill()
            core.wait(timeout=5)
        if command_group is not None:
            with suppress(ProcessLookupError):
                _kill_posix_process_group(command_group)
        if command_pid is not None:
            _wait_for_process_stop(command_pid, timeout=1, starttime=command_starttime)
        if descendant_pid is not None:
            _wait_for_process_stop(
                descendant_pid, timeout=1, starttime=descendant_starttime
            )
