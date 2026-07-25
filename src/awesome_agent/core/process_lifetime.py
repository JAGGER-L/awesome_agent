"""Process-lifetime ownership for Core and every subprocess it creates."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class ProcessTreeGuardError(RuntimeError):
    """Raised when the platform process-tree guard cannot be installed."""


@dataclass(frozen=True, slots=True)
class ProcessTreeGuard:
    """A process-lifetime guard retained until the operating system exits Core."""

    active: bool
    _handle: int | None = None


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


_installed_guard: ProcessTreeGuard | None = None
_inactive_guard = ProcessTreeGuard(active=False)


def install_process_tree_guard() -> ProcessTreeGuard:
    """Install the Windows Core job once; return a no-op guard elsewhere.

    The Windows handle is intentionally never closed from user code. Keeping the
    last handle alive until process teardown makes abnormal and normal Core exits
    equivalent: the kernel closes the handle and terminates every process in the
    job. A non-inheritable handle is sufficient because descendants inherit job
    membership, not ownership of the handle.
    """

    global _installed_guard

    if os.name != "nt":
        return _inactive_guard
    if _installed_guard is not None:
        return _installed_guard

    _installed_guard = _install_windows_job()
    return _installed_guard


def _install_windows_job() -> ProcessTreeGuard:
    win_dll = cast(
        "Callable[..., Any] | None",
        getattr(ctypes, "WinDLL", None),
    )
    if win_dll is None:
        raise ProcessTreeGuardError(
            "Windows process-tree guard ctypes bindings are unavailable."
        )
    kernel32 = win_dll("kernel32", use_last_error=True)

    create_job_object = kernel32.CreateJobObjectW
    create_job_object.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    create_job_object.restype = ctypes.c_void_p

    set_information_job_object = kernel32.SetInformationJobObject
    set_information_job_object.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    set_information_job_object.restype = ctypes.c_int

    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = ctypes.c_void_p

    assign_process_to_job_object = kernel32.AssignProcessToJobObject
    assign_process_to_job_object.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    assign_process_to_job_object.restype = ctypes.c_int

    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    handle = create_job_object(None, None)
    if not handle:
        raise _windows_guard_error("CreateJobObjectW")

    information = _JobObjectExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not set_information_job_object(
        handle,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = _windows_guard_error("SetInformationJobObject")
        close_handle(handle)
        raise error

    if not assign_process_to_job_object(handle, get_current_process()):
        error = _windows_guard_error("AssignProcessToJobObject")
        close_handle(handle)
        raise error

    return ProcessTreeGuard(active=True, _handle=int(handle))


def _windows_guard_error(operation: str) -> ProcessTreeGuardError:
    get_last_error = cast(
        "Callable[[], int] | None",
        getattr(ctypes, "get_last_error", None),
    )
    format_error = cast(
        "Callable[[int], str] | None",
        getattr(ctypes, "FormatError", None),
    )
    if get_last_error is None or format_error is None:
        return ProcessTreeGuardError(
            f"Windows process-tree guard {operation} failed "
            "and Win32 diagnostics are unavailable."
        )
    code = get_last_error()
    detail = format_error(code).strip()
    return ProcessTreeGuardError(
        f"Windows process-tree guard {operation} failed (WinError {code}: {detail})."
    )
