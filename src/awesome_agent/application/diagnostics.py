from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import stat
import threading
import time
from collections import deque
from contextlib import suppress
from pathlib import Path

from awesome_agent.application.middleware import ApplicationObservation
from awesome_agent.config.resource_lock import (
    ResourceLockTimeout,
    exclusive_resource_lock,
)
from awesome_agent.core.filesystem import (
    DirectoryPin,
    lstat_child,
    open_directory,
    remove_child,
)

logger = logging.getLogger(__name__)

_DEFAULT_FILE_COUNT = 5
_DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024
_DEFAULT_QUEUE_CAPACITY = 1_024
_DEFAULT_LOCK_TIMEOUT_SECONDS = 0.25
_DEFAULT_CLOSE_TIMEOUT_SECONDS = 2.0
_MAX_RECORD_BYTES = 64 * 1024
_LOG_FILE_NAME = "application.jsonl"


class ApplicationDiagnosticWriter:
    """Best-effort bounded JSONL diagnostics owned by one Application Session."""

    def __init__(
        self,
        logs_dir: Path,
        *,
        file_count: int = _DEFAULT_FILE_COUNT,
        max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
        queue_capacity: int = _DEFAULT_QUEUE_CAPACITY,
        lock_timeout_seconds: float = _DEFAULT_LOCK_TIMEOUT_SECONDS,
        close_timeout_seconds: float = _DEFAULT_CLOSE_TIMEOUT_SECONDS,
    ) -> None:
        if file_count < 1:
            raise ValueError("Diagnostic file_count must be positive.")
        if max_file_bytes < 1:
            raise ValueError("Diagnostic max_file_bytes must be positive.")
        if queue_capacity < 1:
            raise ValueError("Diagnostic queue_capacity must be positive.")
        if lock_timeout_seconds <= 0 or not math.isfinite(lock_timeout_seconds):
            raise ValueError("Diagnostic lock timeout must be finite and positive.")
        if close_timeout_seconds <= 0 or not math.isfinite(close_timeout_seconds):
            raise ValueError("Diagnostic close timeout must be finite and positive.")

        self._logs_dir = Path(logs_dir)
        self._file_count = file_count
        self._max_file_bytes = max_file_bytes
        self._queue_capacity = queue_capacity
        self._lock_timeout_seconds = lock_timeout_seconds
        self._close_timeout_seconds = close_timeout_seconds
        self._queue: deque[bytes] = deque()
        self._condition = threading.Condition()
        self._accepting = True
        self._closing = False
        self._drain_deadline: float | None = None
        self._closed = threading.Event()
        self._warning_lock = threading.Lock()
        self._warning_emitted = False
        self._thread = threading.Thread(
            target=self._run,
            name="awesome-application-diagnostics",
            daemon=True,
        )
        try:
            self._thread.start()
        except BaseException:
            self._accepting = False
            self._closing = True
            self._closed.set()
            raise

    def try_emit(self, observation: ApplicationObservation) -> None:
        """Admit one already-sanitized record without waiting for filesystem I/O."""

        try:
            encoded = (
                json.dumps(
                    observation.model_dump(mode="json", exclude_none=True),
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                + b"\n"
            )
        except BaseException:
            return
        if len(encoded) > min(self._max_file_bytes, _MAX_RECORD_BYTES):
            return

        with self._condition:
            if not self._accepting:
                return
            if len(self._queue) >= self._queue_capacity:
                return
            self._queue.append(encoded)
            self._condition.notify()

    async def aclose(self) -> None:
        """Stop admission and wait only for the configured bounded drain window."""

        with self._condition:
            if not self._closing:
                self._accepting = False
                self._closing = True
                self._drain_deadline = time.monotonic() + self._close_timeout_seconds
                self._condition.notify_all()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._close_timeout_seconds
        while not self._closed.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.01, remaining))
        if self._closed.is_set():
            return
        with self._condition:
            self._queue.clear()
            self._drain_deadline = time.monotonic()
            self._condition.notify_all()

    def _run(self) -> None:
        try:
            while True:
                line = self._next_line()
                if line is None:
                    return
                try:
                    self._write_line(line)
                except ResourceLockTimeout:
                    # Diagnostics must never turn transient contention into a
                    # permanent loss of observability for this Session.
                    continue
                except BaseException:
                    self._disable()
                    self._warn_once()
                    return
        except BaseException:
            self._disable()
            self._warn_once()
        finally:
            self._closed.set()

    def _next_line(self) -> bytes | None:
        with self._condition:
            while not self._queue and not self._closing:
                self._condition.wait()
            deadline = self._drain_deadline
            if self._closing and (
                not self._queue
                or (deadline is not None and time.monotonic() >= deadline)
            ):
                self._queue.clear()
                return None
            return self._queue.popleft()

    def _disable(self) -> None:
        with self._condition:
            self._accepting = False
            self._closing = True
            self._queue.clear()
            self._drain_deadline = time.monotonic()
            self._condition.notify_all()

    def _warn_once(self) -> None:
        with self._warning_lock:
            if self._warning_emitted:
                return
            self._warning_emitted = True
        try:
            logger.warning(
                "Application diagnostics are unavailable or dropped records."
            )
        except BaseException:
            return

    def _write_line(self, line: bytes) -> None:
        directory = _ensure_safe_directory(self._logs_dir)
        active_name = self._slot_name(0)
        try:
            with exclusive_resource_lock(
                self._logs_dir / active_name,
                timeout_seconds=self._lock_timeout_seconds,
                directory=directory,
            ):
                self._remove_oversized_slots(directory)
                descriptor = _open_safe_append_file(directory, active_name)
                try:
                    size = _repair_partial_line(descriptor)
                    if size + len(line) > self._max_file_bytes:
                        os.close(descriptor)
                        descriptor = -1
                        self._rotate(directory)
                        descriptor = _open_safe_append_file(directory, active_name)
                        size = _repair_partial_line(descriptor)
                    if size + len(line) > self._max_file_bytes:
                        raise OSError(
                            "Diagnostic record exceeds the active file bound."
                        )
                    try:
                        _write_all(descriptor, line)
                    except BaseException:
                        with suppress(OSError):
                            os.ftruncate(descriptor, size)
                        raise
                    if os.fstat(descriptor).st_size > self._max_file_bytes:
                        raise OSError("Diagnostic file exceeded its size bound.")
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
        finally:
            directory.close()

    def _remove_oversized_slots(self, directory: DirectoryPin) -> None:
        for index in range(self._file_count):
            name = self._slot_name(index)
            metadata = _optional_safe_file(directory, name)
            if metadata is not None and metadata.st_size > self._max_file_bytes:
                _unlink_safe_file(directory, name, metadata)

    def _rotate(self, directory: DirectoryPin) -> None:
        oldest = self._slot_name(self._file_count - 1)
        oldest_status = _optional_safe_file(directory, oldest)
        if oldest_status is not None:
            _unlink_safe_file(directory, oldest, oldest_status)
        for index in range(self._file_count - 2, -1, -1):
            source = self._slot_name(index)
            source_status = _optional_safe_file(directory, source)
            if source_status is None:
                continue
            target = self._slot_name(index + 1)
            if _optional_safe_file(directory, target) is not None:
                raise OSError("Diagnostic rotation target was not cleared.")
            _replace_child(directory, source, target)
            target_status = _require_safe_file(directory, target)
            if _file_identity(target_status) != _file_identity(source_status):
                raise OSError("Diagnostic file identity changed during rotation.")

    def _slot_name(self, index: int) -> str:
        if index == 0:
            return _LOG_FILE_NAME
        return f"{_LOG_FILE_NAME}.{index}"


def _ensure_safe_directory(path: Path) -> DirectoryPin:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return open_directory(path)


def _safe_file(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and not _is_link_or_reparse(metadata)
        and int(metadata.st_nlink) == 1
    )


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse)


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        stat.S_IFMT(metadata.st_mode),
        int(getattr(metadata, "st_file_attributes", 0)),
    )


def _optional_safe_file(
    directory: DirectoryPin,
    name: str,
) -> os.stat_result | None:
    try:
        metadata = lstat_child(directory, name)
    except FileNotFoundError:
        return None
    if not _safe_file(metadata):
        raise OSError("Diagnostic path is not a safe regular file.")
    return metadata


def _require_safe_file(directory: DirectoryPin, name: str) -> os.stat_result:
    metadata = lstat_child(directory, name)
    if not _safe_file(metadata):
        raise OSError("Diagnostic path is not a safe regular file.")
    return metadata


def _unlink_safe_file(
    directory: DirectoryPin,
    name: str,
    expected: os.stat_result,
) -> None:
    current = _require_safe_file(directory, name)
    if _file_identity(current) != _file_identity(expected):
        raise OSError("Diagnostic file identity changed before removal.")
    remove_child(directory, name, directory=False)


def _open_safe_append_file(directory: DirectoryPin, name: str) -> int:
    before = _optional_safe_file(directory, name)
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT
    for flag_name in ("O_BINARY", "O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= int(getattr(os, flag_name, 0))
    if os.name == "nt":
        descriptor = os.open(directory.path / name, flags, 0o600)
    else:
        descriptor = os.open(name, flags, 0o600, dir_fd=directory.descriptor)
    try:
        opened = os.fstat(descriptor)
        linked = _require_safe_file(directory, name)
        if not _safe_file(opened) or _file_identity(opened) != _file_identity(linked):
            raise OSError("Diagnostic file changed while it was opened.")
        if before is not None and _file_identity(before) != _file_identity(linked):
            raise OSError("Diagnostic file identity changed before append.")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _replace_child(directory: DirectoryPin, source: str, target: str) -> None:
    if os.name == "nt":
        os.replace(directory.path / source, directory.path / target)
        return
    os.replace(
        source,
        target,
        src_dir_fd=directory.descriptor,
        dst_dir_fd=directory.descriptor,
    )


def _repair_partial_line(descriptor: int) -> int:
    size = int(os.fstat(descriptor).st_size)
    if size == 0:
        return 0
    os.lseek(descriptor, -1, os.SEEK_END)
    if os.read(descriptor, 1) == b"\n":
        return size

    cursor = size
    while cursor > 0:
        count = min(64 * 1024, cursor)
        cursor -= count
        os.lseek(descriptor, cursor, os.SEEK_SET)
        chunk = os.read(descriptor, count)
        marker = chunk.rfind(b"\n")
        if marker >= 0:
            repaired = cursor + marker + 1
            os.ftruncate(descriptor, repaired)
            return repaired
    os.ftruncate(descriptor, 0)
    return 0


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("Diagnostic append did not make progress.")
        remaining = remaining[written:]


__all__ = ["ApplicationDiagnosticWriter"]
