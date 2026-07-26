from __future__ import annotations

import ctypes
import errno
import importlib
import math
import os
import stat
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol, cast

_DEFAULT_TIMEOUT_SECONDS = 10.0
_POLL_INTERVAL_SECONDS = 0.01
_WINDOWS_LOCK_VIOLATION = 33


class ResourceLockUnavailable(RuntimeError):
    def __init__(
        self,
        message: str = "The user-state resource lock is unavailable.",
    ) -> None:
        super().__init__(message)


class ResourceLockTimeout(ResourceLockUnavailable):
    def __init__(self) -> None:
        super().__init__("Timed out waiting for the user-state resource lock.")


class _ReentrantLock(Protocol):
    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool: ...

    def release(self) -> None: ...


class _HeldLocks(threading.local):
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}


class _WindowsOverlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", ctypes.c_uint32),
        ("OffsetHigh", ctypes.c_uint32),
        ("hEvent", ctypes.c_void_p),
    ]


class _WindowsDllLoader(Protocol):
    def __call__(self, name: str, *, use_last_error: bool) -> object: ...


class _WindowsFileLockApi(Protocol):
    def LockFileEx(
        self,
        handle: ctypes.c_void_p,
        flags: int,
        reserved: int,
        bytes_low: int,
        bytes_high: int,
        overlapped: object,
    ) -> int: ...


_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, _ReentrantLock] = {}
_HELD_LOCKS = _HeldLocks()


@contextmanager
def exclusive_resource_lock(
    resource_path: Path,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Serialize one resource transaction across threads and processes."""

    if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
        raise ValueError("timeout_seconds must be finite and positive")
    lock_path = _lock_path(resource_path)
    key = os.fspath(lock_path)
    deadline = time.monotonic() + timeout_seconds
    thread_lock = _thread_lock(key)
    if not thread_lock.acquire(timeout=_remaining(deadline)):
        raise ResourceLockTimeout

    descriptor: int | None = None
    held = _HELD_LOCKS.counts
    try:
        depth = held.get(key, 0)
        if depth > 0:
            held[key] = depth + 1
            try:
                yield
            finally:
                held[key] -= 1
            return

        descriptor = _open_lock_file(lock_path)
        _acquire_platform_lock(descriptor, deadline)
        held[key] = 1
        try:
            yield
        finally:
            held.pop(key, None)
    finally:
        try:
            if descriptor is not None:
                os.close(descriptor)
        finally:
            thread_lock.release()


def _lock_path(resource_path: Path) -> Path:
    expanded = resource_path.expanduser()
    normalized = os.path.normcase(os.path.abspath(os.fspath(expanded)))
    resource = Path(normalized)
    lock_name = (
        f"{resource.name}.lock"
        if resource.name.startswith(".")
        else f".{resource.name}.lock"
    )
    return resource.parent / lock_name


def _thread_lock(key: str) -> _ReentrantLock:
    with _LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


def _open_lock_file(lock_path: Path) -> int:
    descriptor: int | None = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        for flag in ("O_BINARY", "O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW"):
            flags |= getattr(os, flag, 0)
        descriptor = os.open(lock_path, flags, 0o600)
        opened = os.fstat(descriptor)
        linked = os.lstat(lock_path)
        if not _safe_lock_file(linked) or not _same_file(opened, linked):
            raise ResourceLockUnavailable
        if opened.st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except ResourceLockUnavailable:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise ResourceLockUnavailable from error


def _safe_lock_file(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return (
        stat.S_ISREG(metadata.st_mode)
        and not attributes & reparse_flag
        and int(metadata.st_nlink) == 1
    )


def _same_file(opened: os.stat_result, linked: os.stat_result) -> bool:
    return (opened.st_dev, opened.st_ino) == (linked.st_dev, linked.st_ino)


def _acquire_platform_lock(descriptor: int, deadline: float) -> None:
    while True:
        try:
            _try_platform_lock(descriptor)
            return
        except OSError as error:
            if not _lock_would_block(error):
                raise ResourceLockUnavailable from error
            remaining = _remaining(deadline)
            if remaining <= 0:
                raise ResourceLockTimeout from error
            time.sleep(min(_POLL_INTERVAL_SECONDS, remaining))


def _try_platform_lock(descriptor: int) -> None:
    if os.name == "nt":
        _try_windows_lock(descriptor)
        return
    fcntl = cast(Any, importlib.import_module("fcntl"))
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _try_windows_lock(descriptor: int) -> None:
    kernel32 = _windows_file_lock_api()
    overlapped = _WindowsOverlapped()
    handle = _windows_os_handle(descriptor)
    if not kernel32.LockFileEx(
        ctypes.c_void_p(handle),
        0x00000003,
        0,
        1,
        0,
        ctypes.byref(overlapped),
    ):
        raise _last_windows_error()


def _windows_file_lock_api() -> _WindowsFileLockApi:
    load_library = cast(_WindowsDllLoader, vars(ctypes)["WinDLL"])
    return cast(
        _WindowsFileLockApi,
        load_library("kernel32", use_last_error=True),
    )


def _windows_os_handle(descriptor: int) -> int:
    runtime = importlib.import_module("msvcrt")
    get_osfhandle = cast(Callable[[int], int], vars(runtime)["get_osfhandle"])
    return get_osfhandle(descriptor)


def _last_windows_error() -> OSError:
    get_last_error = cast(Callable[[], int], vars(ctypes)["get_last_error"])
    win_error = cast(Callable[[int], OSError], vars(ctypes)["WinError"])
    return win_error(get_last_error())


def _lock_would_block(error: OSError) -> bool:
    return error.errno in {errno.EACCES, errno.EAGAIN} or (
        getattr(error, "winerror", None) == _WINDOWS_LOCK_VIOLATION
    )


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())
