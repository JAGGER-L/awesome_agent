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
from typing import cast

import pytest

import awesome_agent.core.tools.process as process_module
from awesome_agent.core.tools._windows_process_job import WindowsCommandJob
from awesome_agent.core.tools.process import ProcessRunner

_SIGKILL = cast(int, vars(signal).get("SIGKILL", signal.SIGTERM))


def _posix_process_group(pid: int) -> int:
    get_process_group = cast(Callable[[int], int], vars(os)["getpgid"])
    return get_process_group(pid)


def _kill_posix_process_group(pid: int) -> None:
    kill_process_group = cast(Callable[[int, int], None], vars(os)["killpg"])
    kill_process_group(pid, _SIGKILL)


def _process_is_running(pid: int) -> bool:
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
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    status = Path("/proc") / str(pid) / "stat"
    if status.exists():
        try:
            return status.read_text(encoding="utf-8").split()[2] != "Z"
        except OSError:
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


def _wait_for_process_stop(pid: int, *, timeout: float = 5.0) -> None:
    deadline = monotonic() + timeout
    while _process_is_running(pid) and monotonic() < deadline:
        import time

        time.sleep(0.02)
    assert not _process_is_running(pid), pid


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
async def test_process_runner_timeout_reaps_descendant(tmp_path: Path) -> None:
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

    result = await ProcessRunner().run(
        argv=[sys.executable, "-c", parent_source],
        cwd=tmp_path,
        environment=dict(os.environ),
        timeout_seconds=0.5,
        max_output_chars=1_000,
    )

    descendant_pid = _wait_for_pid_file(descendant_pid_file)
    _wait_for_process_stop(descendant_pid)
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
        stderr=subprocess.PIPE,
        text=True,
    )
    command_pid: int | None = None
    descendant_pid: int | None = None
    command_group: int | None = None
    try:
        command_pid = _wait_for_pid_file(command_pid_file)
        descendant_pid = _wait_for_pid_file(descendant_pid_file)
        command_group = _posix_process_group(command_pid)
        assert _process_is_running(command_pid)
        assert _process_is_running(descendant_pid)

        os.kill(core.pid, _SIGKILL)
        core.wait(timeout=5)

        _wait_for_process_stop(command_pid)
        _wait_for_process_stop(descendant_pid)
    finally:
        if core.poll() is None:
            core.kill()
            core.wait(timeout=5)
        if command_group is not None:
            with suppress(ProcessLookupError):
                _kill_posix_process_group(command_group)
        if command_pid is not None:
            _wait_for_process_stop(command_pid, timeout=1)
        if descendant_pid is not None:
            _wait_for_process_stop(descendant_pid, timeout=1)
