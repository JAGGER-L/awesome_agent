from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any, cast

from awesome_agent.core.process_lifetime import install_process_tree_guard


def test_process_tree_guard_is_idempotent() -> None:
    if os.name == "nt":
        source = (
            "from awesome_agent.core.process_lifetime import "
            "install_process_tree_guard;"
            "first=install_process_tree_guard();"
            "second=install_process_tree_guard();"
            "assert first is second;"
            "assert first.active"
        )
        completed = subprocess.run(
            [sys.executable, "-c", source],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
        return

    first = install_process_tree_guard()
    second = install_process_tree_guard()
    assert first is second
    assert first.active is False


def test_process_tree_guard_owns_descendants_until_abnormal_exit(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "descendant.pid"
    helper_source = (
        "import os,subprocess,sys;"
        "from awesome_agent.core.process_lifetime import install_process_tree_guard;"
        "guard=install_process_tree_guard();"
        "child=subprocess.Popen("
        "[sys.executable,'-c','import time; time.sleep(60)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL);"
        f"open({os.fspath(pid_path)!r},'w',encoding='utf-8').write(str(child.pid));"
        "os._exit(73)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", helper_source],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 73, completed.stderr
    child_pid = int(pid_path.read_text(encoding="utf-8"))
    try:
        if os.name == "nt":
            assert _wait_until_stopped(child_pid, timeout=5)
        else:
            assert _process_is_alive(child_pid)
    finally:
        if _process_is_alive(child_pid):
            os.kill(child_pid, signal.SIGTERM)
            _wait_until_stopped(child_pid, timeout=5)


def test_process_tree_guard_nests_inside_an_existing_host_job(
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        assert install_process_tree_guard().active is False
        return

    pid_path = tmp_path / "nested-descendant.pid"
    done_path = tmp_path / "nested-root.done"
    release_path = tmp_path / "host.release"
    root_source = (
        "import os,subprocess,sys;"
        "from awesome_agent.core.process_lifetime import install_process_tree_guard;"
        "install_process_tree_guard();"
        "child=subprocess.Popen("
        "[sys.executable,'-c','import time; time.sleep(60)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL);"
        f"open({os.fspath(pid_path)!r},'w',encoding='utf-8').write(str(child.pid));"
        "os._exit(73)"
    )
    host_source = dedent(
        f"""
        import subprocess
        import sys
        import time
        from pathlib import Path
        from awesome_agent.core.process_lifetime import install_process_tree_guard

        install_process_tree_guard()
        completed = subprocess.run(
            [sys.executable, "-c", {root_source!r}],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        Path({os.fspath(done_path)!r}).write_text(
            f"{{completed.returncode}}\\n{{completed.stderr}}",
            encoding="utf-8",
        )
        deadline = time.monotonic() + 10
        release = Path({os.fspath(release_path)!r})
        while not release.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not release.exists():
            raise SystemExit("test host release timed out")
        """
    )
    host = subprocess.Popen(
        [sys.executable, "-c", host_source],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid: int | None = None
    try:
        assert _wait_until_path_exists(done_path, timeout=10)
        return_code, _, stderr = done_path.read_text(encoding="utf-8").partition("\n")
        assert return_code == "73", stderr
        child_pid = int(pid_path.read_text(encoding="utf-8"))
        assert host.poll() is None
        assert _wait_until_stopped(child_pid, timeout=5)
    finally:
        release_path.touch()
        try:
            _, host_stderr = host.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            host.terminate()
            _, host_stderr = host.communicate(timeout=5)
        if child_pid is not None and _process_is_alive(child_pid):
            os.kill(child_pid, signal.SIGTERM)
        assert host.returncode == 0, host_stderr


def _wait_until_stopped(pid: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_is_alive(pid):
            return True
        time.sleep(0.02)
    return not _process_is_alive(pid)


def _wait_until_path_exists(path: Path, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return path.exists()


def _process_is_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        stat_path = Path("/proc") / str(pid) / "stat"
        if stat_path.exists():
            try:
                return stat_path.read_text(encoding="utf-8").split()[2] != "Z"
            except OSError:
                return True
        return True

    synchronize = 0x00100000
    process_query_limited_information = 0x1000
    wait_timeout = 0x00000102
    win_dll = cast("Callable[..., Any] | None", getattr(ctypes, "WinDLL", None))
    assert win_dll is not None
    kernel32 = win_dll("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p
    handle = open_process(
        synchronize | process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return False
    try:
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        wait_for_single_object.restype = ctypes.c_uint32
        return int(wait_for_single_object(handle, 0)) == wait_timeout
    finally:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        close_handle(handle)
