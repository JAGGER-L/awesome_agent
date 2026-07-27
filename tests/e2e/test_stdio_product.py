from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from awesome_agent.storage import APPLICATION_SCHEMA_VERSION
from awesome_agent.version import PRODUCT_VERSION


def _value(frame: dict[str, Any]) -> dict[str, Any]:
    result = frame["result"]
    assert isinstance(result, dict)
    assert result["ok"] is True
    value = result["value"]
    assert isinstance(value, dict)
    return value


def _transition_thread_id(frame: dict[str, Any]) -> str:
    payload = _value(frame)["payload"]
    assert payload["kind"] == "thread_transition"
    transition = payload["transition"]
    thread_id = transition["thread"]["view"]["thread"]["id"]
    assert transition["application"]["current_thread_id"] == thread_id
    return str(thread_id)


class Client:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.identifier = 0
        self.events: list[dict[str, Any]] = []
        self.stdout_frames: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        params: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        self.identifier += 1
        identifier = self.identifier
        assert self.process.stdin is not None
        self.process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "method": method,
                    "params": params or {},
                }
            ).encode()
            + b"\n"
        )
        await self.process.stdin.drain()
        while True:
            frame = await self._read()
            if frame.get("method") == "event":
                self.events.append(frame["params"])
                continue
            if frame.get("id") == identifier:
                return frame

    async def wait_operation(self, operation_id: str) -> list[dict[str, Any]]:
        observed = [
            event for event in self.events if event.get("operation_id") == operation_id
        ]
        terminal = {
            "operation.completed",
            "operation.failed",
            "operation.cancelled",
        }
        if observed and observed[-1]["event_type"] in terminal:
            return observed
        while True:
            frame = await self._read()
            if frame.get("method") != "event":
                continue
            event = frame["params"]
            self.events.append(event)
            if event.get("operation_id") == operation_id:
                observed.append(event)
                if event["event_type"] in terminal:
                    return observed

    async def _read(self) -> dict[str, Any]:
        assert self.process.stdout is not None
        raw = await asyncio.wait_for(self.process.stdout.readline(), timeout=10)
        assert raw, await self.stderr()
        frame: object = json.loads(raw)
        assert isinstance(frame, dict)
        self.stdout_frames.append(frame)
        return frame

    async def stderr(self) -> str:
        if self.process.stderr is None:
            return ""
        try:
            raw = await asyncio.wait_for(self.process.stderr.read(), timeout=0.1)
        except TimeoutError:
            return ""
        return raw.decode(errors="replace")


async def _spawn(
    *,
    home: Path,
    workspace: Path,
    provider: str,
) -> Client:
    environment = dict(os.environ)
    environment.update(
        {
            "AWESOME_HOME": str(home),
            "AWESOME_WORKSPACE": str(workspace),
            "AWESOME_FAKE_PROVIDER": provider,
            "PYTHONUNBUFFERED": "1",
        }
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.fixtures.stdio_fake_services",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
        cwd=Path.cwd(),
    )
    return Client(process)


def _write_schema_version(home: Path, version: int) -> Path:
    state_dir = home / "state"
    state_dir.mkdir(parents=True)
    database = state_dir / "application.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_marker VALUES ('legacy')")
        connection.execute(f"PRAGMA user_version = {version}")
        connection.commit()
    return database


def _schema_version(database: Path) -> int:
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute("PRAGMA user_version").fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("deepseek", "deepseek/deepseek-v4-flash"),
        ("kimi", "kimi/kimi-k2.6"),
    ],
)
async def test_stdio_full_flow_and_restart(
    tmp_path: Path,
    provider: str,
    model: str,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("fixture source", encoding="utf-8")
    client = await _spawn(home=home, workspace=workspace, provider=provider)

    initialized = await client.request(
        "initialize",
        {
            "protocol_version": 4,
            "client_name": "awesome",
            "client_version": PRODUCT_VERSION,
        },
    )
    initialized_value = _value(initialized)
    interaction_id = initialized_value["interaction_id"]
    assert initialized_value["status"] == "trust_required"
    trusted = await client.request(
        "interaction.respond",
        {"interaction_id": interaction_id, "decision": "trust"},
    )
    assert _value(trusted)["accepted"] is True
    state = await client.request("application.getState")
    assert _value(state).get("current_thread_id") is None
    assert _value(state)["initialized"] is True
    created = await client.request("command.execute", {"name": "new"})
    thread_id = _transition_thread_id(created)

    model_selected = await client.request(
        "command.execute",
        {"name": "model", "arguments": [provider, model]},
    )
    assert _value(model_selected)["payload"]["model"] == model
    second_thread_id = _transition_thread_id(
        await client.request("command.execute", {"name": "new"})
    )
    mismatched_turn = await client.request(
        "turn.submit",
        {
            "thread_id": thread_id,
            "content": "must not run on an unselected thread",
            "client_message_id": "client_e2e_cross_thread",
        },
    )
    assert mismatched_turn["result"]["ok"] is False
    assert mismatched_turn["result"]["error"]["code"] == "invalid_arguments"
    mismatched_direct = await client.request(
        "direct.execute",
        {"thread_id": thread_id, "command": "echo must-not-run"},
    )
    assert mismatched_direct["result"]["ok"] is False
    assert mismatched_direct["result"]["error"]["code"] == "invalid_arguments"
    assert (
        _transition_thread_id(
            await client.request(
                "command.execute",
                {"name": "resume", "arguments": [thread_id]},
            )
        )
        == thread_id
    )
    submitted = await client.request(
        "turn.submit",
        {
            "thread_id": thread_id,
            "content": "use tool to inspect @sample.txt",
            "client_message_id": "client_e2e_inspect",
        },
    )
    operation_id = _value(submitted)["operation_id"]
    turn_events = await client.wait_operation(operation_id)
    assert sum(event["event_type"] == "turn.completed" for event in turn_events) == 1
    assert any(event["event_type"] == "tool.completed" for event in turn_events)

    direct = await client.request(
        "direct.execute",
        {"thread_id": thread_id, "command": "echo direct-e2e"},
    )
    direct_events = await client.wait_operation(_value(direct)["operation_id"])
    assert direct_events[-1]["event_type"] == "operation.completed"
    read = await client.request("thread.read", {"thread_id": thread_id})
    assert _value(read)["view"]["entries"][-1]["kind"] == "direct_command"
    assert "direct-e2e" in _value(read)["view"]["entries"][-1]["content"]

    waiting = await client.request(
        "turn.submit",
        {
            "thread_id": thread_id,
            "content": "wait forever",
            "client_message_id": "client_e2e_wait",
        },
    )
    cancelled = await client.request(
        "operation.cancel",
        {"operation_id": _value(waiting)["operation_id"]},
    )
    assert _value(cancelled)["cancelled"] is True
    cancel_events = await client.wait_operation(_value(waiting)["operation_id"])
    assert cancel_events[-1]["event_type"] == "operation.cancelled"

    listed = await client.request("thread.list")
    assert {item["id"] for item in _value(listed)["threads"]} == {
        thread_id,
        second_thread_id,
    }
    shutdown = await client.request("shutdown")
    assert _value(shutdown) == {"stopped": True}
    await asyncio.wait_for(client.process.wait(), timeout=10)
    assert client.process.returncode == 0, await client.stderr()
    assert len({event["event_id"] for event in client.events}) == len(client.events)
    sequences = [event["sequence"] for event in client.events]
    assert sequences == sorted(sequences)
    assert "fake-key" not in json.dumps(client.stdout_frames)

    restarted = await _spawn(home=home, workspace=workspace, provider=provider)
    ready = await restarted.request(
        "initialize",
        {
            "protocol_version": 4,
            "client_name": "awesome",
            "client_version": PRODUCT_VERSION,
        },
    )
    assert _value(ready)["status"] == "ready"
    resumed = await restarted.request(
        "command.execute",
        {"name": "resume", "arguments": [thread_id]},
    )
    assert _transition_thread_id(resumed) == thread_id
    restored = await restarted.request("thread.read", {"thread_id": thread_id})
    assert any(
        entry["kind"] == "direct_command"
        for entry in _value(restored)["view"]["entries"]
    )
    assert all(
        event["event_type"] != "assistant.reasoning.delta" for event in restarted.events
    )
    await restarted.request("shutdown")
    await asyncio.wait_for(restarted.process.wait(), timeout=10)


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_schema", [2, 6])
async def test_stdio_resets_older_state_then_continues_to_workspace_trust(
    tmp_path: Path,
    legacy_schema: int,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = _write_schema_version(home, legacy_schema)
    config = home / "config.yaml"
    config.write_text("providers: {}\n", encoding="utf-8")
    client = await _spawn(home=home, workspace=workspace, provider="deepseek")

    initialized = await client.request(
        "initialize",
        {
            "protocol_version": 4,
            "client_name": "awesome",
            "client_version": PRODUCT_VERSION,
        },
    )
    reset_required = _value(initialized)
    assert reset_required["status"] == "state_reset_required"
    reset_interaction_id = reset_required["interaction_id"]
    reset_events = [
        event
        for event in client.events
        if event["event_type"] == "interaction.required"
    ]
    assert reset_events[-1]["payload"]["interaction_kind"] == "state_reset"

    reset = await client.request(
        "interaction.respond",
        {
            "interaction_id": reset_interaction_id,
            "decision": "reset_state",
        },
    )
    assert _value(reset) == {"accepted": True, "status": "resolved"}

    after_reset = await client.request(
        "initialize",
        {
            "protocol_version": 4,
            "client_name": "awesome",
            "client_version": PRODUCT_VERSION,
        },
    )
    trust_required = _value(after_reset)
    assert trust_required["status"] == "trust_required"
    trusted = await client.request(
        "interaction.respond",
        {
            "interaction_id": trust_required["interaction_id"],
            "decision": "trust",
        },
    )
    assert _value(trusted) == {"accepted": True, "status": "resolved"}
    assert _schema_version(database) == APPLICATION_SCHEMA_VERSION == 8
    with closing(sqlite3.connect(database)) as connection:
        marker = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'legacy_marker'"
        ).fetchone()
    assert marker is None
    assert config.read_text(encoding="utf-8") == "providers: {}\n"

    await client.request("shutdown")
    await asyncio.wait_for(client.process.wait(), timeout=10)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_stdio_rejects_newer_state_without_offering_reset(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = _write_schema_version(home, 9)
    before = database.read_bytes()
    client = await _spawn(home=home, workspace=workspace, provider="deepseek")

    initialized = await client.request(
        "initialize",
        {
            "protocol_version": 4,
            "client_name": "awesome",
            "client_version": PRODUCT_VERSION,
        },
    )

    assert initialized["result"] == {
        "ok": False,
        "error": {
            "code": "state_created_by_newer_version",
            "message": (
                "Local state was created by a newer Awesome version. "
                "Upgrade Awesome to continue."
            ),
            "retryable": False,
            "data": {
                "found_schema": 9,
                "expected_schema": APPLICATION_SCHEMA_VERSION,
                "state_directory": str((home / "state").resolve()),
            },
        },
    }
    assert not any(
        event.get("payload", {}).get("interaction_kind") == "state_reset"
        for event in client.events
    )
    assert database.read_bytes() == before

    await client.request("shutdown")
    await asyncio.wait_for(client.process.wait(), timeout=10)
