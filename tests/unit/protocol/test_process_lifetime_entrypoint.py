from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest

import awesome_agent.protocol.stdio as stdio
from awesome_agent.core.process_lifetime import ProcessTreeGuardError


def test_core_main_installs_process_guard_before_starting_async_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def install() -> object:
        calls.append("guard")
        return object()

    def run(coroutine: Coroutine[Any, Any, None]) -> None:
        calls.append("asyncio")
        coroutine.close()

    monkeypatch.setattr(stdio, "install_process_tree_guard", install)
    monkeypatch.setattr(asyncio, "run", run)

    stdio.main()

    assert calls == ["guard", "asyncio"]


def test_core_main_fails_closed_when_process_guard_cannot_be_installed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def install() -> object:
        raise ProcessTreeGuardError(
            "Windows process-tree guard AssignProcessToJobObject failed "
            "(WinError 5: Access is denied.)."
        )

    def unexpected_run(coroutine: object) -> None:
        del coroutine
        pytest.fail("the async runtime must not start without the process guard")

    monkeypatch.setattr(stdio, "install_process_tree_guard", install)
    monkeypatch.setattr(asyncio, "run", unexpected_run)

    with pytest.raises(SystemExit) as raised:
        stdio.main()

    assert raised.value.code == 1
    assert capsys.readouterr().err == (
        "awesome-core: fatal process lifetime initialization failure: "
        "Windows process-tree guard AssignProcessToJobObject failed "
        "(WinError 5: Access is denied.).\n"
    )
