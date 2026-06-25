from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SupervisorResult:
    service: str
    return_code: int


def run_supervisor(
    *,
    host: str,
    port: int,
    shutdown_timeout: float,
) -> SupervisorResult:
    children = {
        "api": _start_child(
            [
                sys.executable,
                "-m",
                "awesome_agent.cli.app",
                "serve",
                "--host",
                host,
                "--port",
                str(port),
            ]
        ),
        "worker": _start_child(
            [
                sys.executable,
                "-m",
                "awesome_agent.cli.app",
                "worker",
            ]
        ),
    }
    result = SupervisorResult(service="supervisor", return_code=0)
    try:
        while True:
            for name, child in children.items():
                return_code = child.poll()
                if return_code is not None:
                    result = SupervisorResult(
                        service=name,
                        return_code=return_code,
                    )
                    return result
            time.sleep(0.2)
    except KeyboardInterrupt:
        return result
    finally:
        _stop_children(children.values(), timeout=shutdown_timeout)


def _start_child(command: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        start_new_session=os.name != "nt",
    )


def _stop_children(
    children: Iterable[subprocess.Popen[bytes]],
    *,
    timeout: float,
) -> None:
    processes = list(children)
    for child in processes:
        if child.poll() is None:
            _request_stop(child)
    deadline = time.monotonic() + timeout
    for child in processes:
        remaining = max(0.0, deadline - time.monotonic())
        if child.poll() is None:
            try:
                child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                child.kill()
    for child in processes:
        if child.poll() is None:
            child.wait()


def _request_stop(child: subprocess.Popen[bytes]) -> None:
    ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
    if os.name == "nt" and ctrl_break is not None:
        child.send_signal(ctrl_break)
        return
    child.terminate()
