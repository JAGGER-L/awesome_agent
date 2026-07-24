import asyncio
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from time import monotonic

import pytest

import awesome_agent.core.tools.process as process_module
from awesome_agent.core.tools.process import ProcessRunner


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
    grandchild_source = "import time; time.sleep(5)"
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
                timeout_seconds=0.2,
                max_output_chars=1000,
            )
            child_pid = int(result.stdout.strip().splitlines()[0])
            deadline = time.monotonic() + 1
            while is_alive(child_pid) and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert not is_alive(child_pid), child_pid
            assert result.stdout_truncated
            current = asyncio.current_task()
            pending = [
                task
                for task in asyncio.all_tasks()
                if task is not current and not task.done()
            ]
            assert pending == []
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
