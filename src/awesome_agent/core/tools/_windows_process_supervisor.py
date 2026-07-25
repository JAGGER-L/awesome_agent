"""Windows command guardian released only after Job Object assignment.

This helper must stay dependency-free: it is launched as a private child of
Core, waits on a named event, and cannot create the requested process until
Core has assigned the helper to the per-command Job Object. The target and all
of its descendants then inherit that job membership atomically.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_INFINITE = 0xFFFFFFFF
_STATUS_SPAWNED = b"S"
_STATUS_ERROR = b"E"


def _wait_for_release(event_name: str) -> bool:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        return False
    kernel32: Any = win_dll("kernel32", use_last_error=True)
    kernel32.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.OpenEventW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenEventW(_SYNCHRONIZE, 0, event_name)
    if not handle:
        return False
    try:
        return int(kernel32.WaitForSingleObject(handle, _INFINITE)) == _WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def _write_status(path: Path, kind: bytes, value: int = 0) -> None:
    payload = kind + value.to_bytes(4, byteorder="big", signed=True)
    staging_path = path.with_name(f"{path.name}.tmp")
    try:
        with staging_path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging_path, path)
    finally:
        with suppress(FileNotFoundError):
            staging_path.unlink()


def _run(event_name: str, status_path: Path, argv: list[str]) -> int:
    if not _wait_for_release(event_name):
        return 125
    try:
        target = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as error:
        _write_status(status_path, _STATUS_ERROR, error.errno or 5)
        return 127
    try:
        _write_status(status_path, _STATUS_SPAWNED)
    except OSError:
        # Core cannot safely distinguish a spawned target from a failed
        # supervisor without the status handshake. Stop the direct child; the
        # per-command Job Object provides the descendant backstop.
        target.kill()
        target.wait()
        return 126
    return target.wait()


def main() -> int:
    if len(sys.argv) < 5 or sys.argv[3] != "--":
        return 125
    return _run(sys.argv[1], Path(sys.argv[2]), sys.argv[4:])


if __name__ == "__main__":
    raise SystemExit(main())
