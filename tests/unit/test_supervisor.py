from __future__ import annotations

import subprocess
from typing import cast

import pytest

from awesome_agent.runtime import supervisor


class FakeProcess:
    def __init__(self, return_code: int | None = None) -> None:
        self.return_code = return_code
        self.signals: list[int] = []
        self.terminated = False
        self.killed = False
        self.wait_timeout: float | None = None

    def poll(self) -> int | None:
        return self.return_code

    def send_signal(self, value: int) -> None:
        self.signals.append(value)
        self.return_code = 0

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = 0

    def kill(self) -> None:
        self.killed = True
        self.return_code = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeout = timeout
        if self.return_code is None:
            raise subprocess.TimeoutExpired("fake", timeout or 0)
        return self.return_code


def test_supervisor_reports_child_exit_and_stops_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeProcess()
    worker = FakeProcess(return_code=7)
    children = iter([api, worker])
    monkeypatch.setattr(supervisor, "_start_child", lambda _: next(children))
    monkeypatch.setattr(
        supervisor,
        "_request_stop",
        lambda child: child.terminate(),
    )

    result = supervisor.run_supervisor(
        host="127.0.0.1",
        port=8000,
        shutdown_timeout=1,
    )

    assert result.service == "worker"
    assert result.return_code == 7
    assert api.terminated


def test_supervisor_passes_public_bind_consent_to_api_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeProcess(return_code=0)
    worker = FakeProcess()
    commands: list[list[str]] = []
    children = iter([api, worker])

    def start_child(command: list[str]) -> FakeProcess:
        commands.append(command)
        return next(children)

    monkeypatch.setattr(supervisor, "_start_child", start_child)

    supervisor.run_supervisor(
        host="0.0.0.0",
        port=8000,
        shutdown_timeout=1,
        unsafe_bind_public=True,
    )

    assert "--unsafe-bind-public" in commands[0]


def test_stop_children_kills_process_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = FakeProcess()

    def ignore_terminate(self: FakeProcess) -> None:
        self.terminated = True

    monkeypatch.setattr(FakeProcess, "terminate", ignore_terminate)
    monkeypatch.setattr(
        supervisor,
        "_request_stop",
        lambda process: process.terminate(),
    )

    supervisor._stop_children(
        [cast(subprocess.Popen[bytes], child)],
        timeout=0,
    )

    assert child.terminated
    assert child.killed
