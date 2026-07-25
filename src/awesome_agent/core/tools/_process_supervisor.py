from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from typing import cast

_LEASE_POLL_SECONDS = 0.02
_DESCENDANT_TERM_GRACE_SECONDS = 0.1
_EVENT_SPAWNED = b"S"
_EVENT_SPAWN_ERROR = b"E"
_EVENT_RESULT = b"R"
_SIGKILL = cast(int, vars(signal).get("SIGKILL", signal.SIGTERM))


def _write_event(fd: int, kind: bytes, value: int | None = None) -> bool:
    payload = kind
    if value is not None:
        payload += value.to_bytes(4, byteorder="big", signed=True)
    try:
        os.write(fd, payload)
    except OSError:
        return False
    return True


def _kill_own_process_group(sig: int) -> None:
    kill_group = cast(Callable[[int, int], None], vars(os)["killpg"])
    get_process_group = cast(Callable[[], int], vars(os)["getpgrp"])
    with suppress(ProcessLookupError):
        kill_group(get_process_group(), sig)


def _close_output_handles() -> None:
    for fd in (sys.stdout.fileno(), sys.stderr.fileno()):
        with suppress(OSError):
            os.close(fd)


def _lease_was_lost(lease_fd: int, timeout: float) -> bool:
    readable, _, _ = select.select([lease_fd], [], [], timeout)
    if not readable:
        return False
    try:
        return os.read(lease_fd, 1) == b""
    except OSError:
        return True


def _run(lease_fd: int, event_fd: int, argv: list[str]) -> int:
    os.set_inheritable(lease_fd, False)
    os.set_inheritable(event_fd, False)
    try:
        target = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as error:
        error_number = error.errno if error.errno is not None else 5
        _write_event(event_fd, _EVENT_SPAWN_ERROR, error_number)
        return 127

    if not _write_event(event_fd, _EVENT_SPAWNED):
        _kill_own_process_group(_SIGKILL)
        return 125

    while target.poll() is None:
        if _lease_was_lost(lease_fd, _LEASE_POLL_SECONDS):
            _kill_own_process_group(_SIGKILL)
            return 125

    return_code = target.wait()
    if not _write_event(event_fd, _EVENT_RESULT, return_code):
        _kill_own_process_group(_SIGKILL)
        return 125
    with suppress(OSError):
        os.close(event_fd)
    _close_output_handles()

    # The command root is complete, so no process other than this guardian may
    # outlive the command cleanup domain. Ignore the graceful group signal in
    # the guardian itself, then force-kill the whole group after a short grace.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    _kill_own_process_group(signal.SIGTERM)
    time.sleep(_DESCENDANT_TERM_GRACE_SECONDS)
    _kill_own_process_group(_SIGKILL)
    return 0


def main() -> int:
    if len(sys.argv) < 5 or sys.argv[3] != "--":
        return 125
    try:
        lease_fd = int(sys.argv[1])
        event_fd = int(sys.argv[2])
    except ValueError:
        return 125
    return _run(lease_fd, event_fd, sys.argv[4:])


if __name__ == "__main__":
    raise SystemExit(main())
