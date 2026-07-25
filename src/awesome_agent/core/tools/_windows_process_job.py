"""Per-command Windows Job Object ownership for :mod:`process`.

The command supervisor is assigned to the job before it is released to spawn
the requested executable. Descendants therefore inherit membership without a
race, and closing the job cannot depend on a root PID that may already have
exited.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100


class WindowsCommandJobError(RuntimeError):
    """A command could not be placed in its Windows cleanup domain."""


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


@dataclass(slots=True)
class WindowsCommandJob:
    event_name: str
    _kernel32: Any
    _job_handle: int
    _event_handle: int
    _closed: bool = False

    @classmethod
    def create(cls) -> WindowsCommandJob:
        if os.name != "nt":
            raise WindowsCommandJobError("Windows command jobs require Windows.")
        win_dll = cast(
            "Callable[..., Any] | None",
            getattr(ctypes, "WinDLL", None),
        )
        if win_dll is None:
            raise WindowsCommandJobError("Win32 Job Object APIs are unavailable.")
        kernel32: Any = win_dll("kernel32", use_last_error=True)
        _configure_bindings(kernel32)

        job_handle = kernel32.CreateJobObjectW(None, None)
        if not job_handle:
            raise _windows_error("CreateJobObjectW")
        information = _JobObjectExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not kernel32.SetInformationJobObject(
            job_handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            error = _windows_error("SetInformationJobObject")
            kernel32.CloseHandle(job_handle)
            raise error

        event_name = f"Local\\AwesomeAgentCommand-{uuid4().hex}"
        event_handle = kernel32.CreateEventW(None, 1, 0, event_name)
        if not event_handle:
            error = _windows_error("CreateEventW")
            kernel32.CloseHandle(job_handle)
            raise error
        return cls(
            event_name=event_name,
            _kernel32=kernel32,
            _job_handle=int(job_handle),
            _event_handle=int(event_handle),
        )

    def assign(self, pid: int) -> None:
        """Make the waiting supervisor the root of this command job.

        A Core process can already belong to its process-lifetime job. Windows
        8 and later permit this second assignment as a nested child job when,
        as here, neither job applies UI restrictions. We deliberately do not
        request ``CREATE_BREAKAWAY_FROM_JOB``: failing closed is safer than
        silently starting a command outside either cleanup domain.
        """

        self._require_open()
        process_handle = self._kernel32.OpenProcess(
            _PROCESS_TERMINATE | _PROCESS_SET_QUOTA,
            0,
            pid,
        )
        if not process_handle:
            raise _windows_error("OpenProcess")
        try:
            if not self._kernel32.AssignProcessToJobObject(
                self._job_handle,
                process_handle,
            ):
                raise _windows_error("AssignProcessToJobObject")
        finally:
            self._kernel32.CloseHandle(process_handle)

    def release_supervisor(self) -> None:
        self._require_open()
        if not self._kernel32.SetEvent(self._event_handle):
            raise _windows_error("SetEvent")

    def terminate(self) -> None:
        if self._closed:
            return
        if not self._kernel32.TerminateJobObject(self._job_handle, 1):
            raise _windows_error("TerminateJobObject")

    def close(self) -> None:
        if self._closed:
            return
        self._kernel32.CloseHandle(self._event_handle)
        self._kernel32.CloseHandle(self._job_handle)
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise WindowsCommandJobError("Windows command job is already closed.")


def _configure_bindings(kernel32: Any) -> None:
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_wchar_p,
    ]
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.SetEvent.argtypes = [ctypes.c_void_p]
    kernel32.SetEvent.restype = ctypes.c_int
    kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateJobObject.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int


def _last_error() -> int:
    get_last_error = cast(
        "Callable[[], int] | None",
        getattr(ctypes, "get_last_error", None),
    )
    return int(get_last_error()) if get_last_error is not None else 0


def _windows_error(
    operation: str,
    *,
    code: int | None = None,
) -> WindowsCommandJobError:
    error_code = _last_error() if code is None else code
    format_error = cast(
        "Callable[[int], str] | None",
        getattr(ctypes, "FormatError", None),
    )
    detail = (
        str(format_error(error_code)).strip()
        if format_error is not None and error_code
        else "Win32 diagnostics unavailable"
    )
    return WindowsCommandJobError(
        f"Windows command job {operation} failed (WinError {error_code}: {detail})."
    )
