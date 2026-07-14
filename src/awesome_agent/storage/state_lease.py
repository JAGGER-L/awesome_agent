from __future__ import annotations

import ctypes
import importlib
import os
from enum import StrEnum
from pathlib import Path
from typing import Any, cast


class StateLeaseMode(StrEnum):
    SHARED = "shared"
    EXCLUSIVE = "exclusive"


class StateLeaseUnavailable(RuntimeError):
    def __init__(self, home: Path, mode: StateLeaseMode) -> None:
        resolved = home.expanduser().resolve()
        super().__init__(f"{mode.value.title()} state ownership is unavailable.")
        self.home = resolved
        self.mode = mode


class StateLease:
    def __init__(
        self,
        *,
        home: Path,
        descriptor: int,
        mode: StateLeaseMode,
        platform_token: object | None,
    ) -> None:
        self._home = home
        self._descriptor: int | None = descriptor
        self._mode = mode
        self._platform_token = platform_token

    @classmethod
    def acquire(cls, home: Path, mode: StateLeaseMode) -> StateLease:
        resolved_home = home.expanduser().resolve()
        descriptor: int | None = None
        try:
            resolved_home.mkdir(parents=True, exist_ok=True)
            lock_path = resolved_home / ".state.lock"
            if lock_path.is_symlink():
                raise StateLeaseUnavailable(resolved_home, mode)
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(lock_path, flags, 0o600)
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            token = _lock(descriptor, mode)
        except StateLeaseUnavailable:
            raise
        except OSError as error:
            if descriptor is not None:
                os.close(descriptor)
            raise StateLeaseUnavailable(resolved_home, mode) from error
        assert descriptor is not None
        return cls(
            home=resolved_home,
            descriptor=descriptor,
            mode=mode,
            platform_token=token,
        )

    @property
    def home(self) -> Path:
        return self._home

    @property
    def mode(self) -> StateLeaseMode:
        return self._mode

    @property
    def active(self) -> bool:
        return self._descriptor is not None

    def downgrade(self) -> None:
        descriptor = self._require_descriptor()
        if self._mode is StateLeaseMode.SHARED:
            return
        try:
            if os.name == "nt":
                _unlock(descriptor, self._platform_token)
                self._platform_token = _lock(descriptor, StateLeaseMode.SHARED)
            else:
                self._platform_token = _lock(descriptor, StateLeaseMode.SHARED)
        except OSError as error:
            os.close(descriptor)
            self._descriptor = None
            self._platform_token = None
            raise StateLeaseUnavailable(
                self._home,
                StateLeaseMode.SHARED,
            ) from error
        self._mode = StateLeaseMode.SHARED

    def close(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        try:
            _unlock(descriptor, self._platform_token)
        finally:
            os.close(descriptor)
            self._descriptor = None
            self._platform_token = None

    def _require_descriptor(self) -> int:
        if self._descriptor is None:
            raise RuntimeError("State lease is closed.")
        return self._descriptor

    def __enter__(self) -> StateLease:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class _WindowsOverlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", ctypes.c_uint32),
        ("OffsetHigh", ctypes.c_uint32),
        ("hEvent", ctypes.c_void_p),
    ]


def _lock(descriptor: int, mode: StateLeaseMode) -> object | None:
    if os.name == "nt":
        return _lock_windows(descriptor, mode)
    fcntl = cast(Any, importlib.import_module("fcntl"))

    operation = fcntl.LOCK_NB
    operation |= fcntl.LOCK_SH if mode is StateLeaseMode.SHARED else fcntl.LOCK_EX
    fcntl.flock(descriptor, operation)
    return None


def _unlock(descriptor: int, token: object | None) -> None:
    if os.name == "nt":
        if not isinstance(token, _WindowsOverlapped):
            raise RuntimeError("Windows state lease token is missing.")
        _unlock_windows(descriptor, token)
        return
    fcntl = cast(Any, importlib.import_module("fcntl"))

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _lock_windows(
    descriptor: int,
    mode: StateLeaseMode,
) -> _WindowsOverlapped:
    import msvcrt

    kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    overlapped = _WindowsOverlapped()
    flags = 0x00000001
    if mode is StateLeaseMode.EXCLUSIVE:
        flags |= 0x00000002
    handle = msvcrt.get_osfhandle(descriptor)
    if not kernel32.LockFileEx(
        ctypes.c_void_p(handle),
        flags,
        0,
        1,
        0,
        ctypes.byref(overlapped),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return overlapped


def _unlock_windows(descriptor: int, token: _WindowsOverlapped) -> None:
    import msvcrt

    kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    handle = msvcrt.get_osfhandle(descriptor)
    if not kernel32.UnlockFileEx(
        ctypes.c_void_p(handle),
        0,
        1,
        0,
        ctypes.byref(token),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
