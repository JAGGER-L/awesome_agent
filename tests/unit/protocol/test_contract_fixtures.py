from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from awesome_agent.application.commands import (
    COMMAND_OWNERS,
    CommandIntent,
    CommandName,
)
from awesome_agent.application.contracts import (
    ApplicationResult,
    ProductErrorCode,
    ProviderCredentialSetRequest,
    ThreadListQuery,
    ThreadReadQuery,
)
from awesome_agent.core.events import EventEnvelope, EventType
from awesome_agent.protocol.jsonrpc import JsonRpcDispatcher
from awesome_agent.version import PRODUCT_VERSION

ROOT = Path(__file__).parents[3]
FIXTURES = ROOT / "protocol" / "fixtures" / "v1"


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_manifest_freezes_complete_protocol_inventory_and_hashes() -> None:
    manifest = _load("manifest.json")

    assert manifest["fixture_version"] == 1
    assert manifest["product_version"] == PRODUCT_VERSION
    assert manifest["protocol_version"] == 1
    assert set(manifest["methods"]) == {
        "initialize",
        "application.getState",
        "thread.list",
        "thread.read",
        "turn.submit",
        "direct.execute",
        "command.execute",
        "provider.credential.set",
        "interaction.respond",
        "operation.cancel",
        "shutdown",
    }
    assert set(manifest["event_types"]) == {item.value for item in EventType}
    assert manifest["command_owners"] == {
        name.value: COMMAND_OWNERS[name].value for name in CommandName
    }
    assert "editor" not in manifest["command_owners"]
    assert "details" not in manifest["command_owners"]

    for name, expected_hash in manifest["files"].items():
        content = (FIXTURES / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == expected_hash


def test_fixture_generator_is_byte_deterministic() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/generate_protocol_fixtures.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


class _FixtureFacade:
    def __init__(self, result: dict[str, object]) -> None:
        self._result = ApplicationResult[dict[str, object]].model_validate(result)

    async def initialize(self) -> ApplicationResult[dict[str, object]]:
        return self._result

    async def get_state(self) -> ApplicationResult[dict[str, object]]:
        return self._result

    async def list_threads(
        self, query: ThreadListQuery
    ) -> ApplicationResult[dict[str, object]]:
        del query
        return self._result

    async def read_thread(
        self, query: ThreadReadQuery
    ) -> ApplicationResult[dict[str, object]]:
        del query
        return self._result

    async def submit_turn(
        self, thread_id: str, content: str
    ) -> ApplicationResult[dict[str, object]]:
        del thread_id, content
        return self._result

    async def execute_direct(
        self, thread_id: str, command: str
    ) -> ApplicationResult[dict[str, object]]:
        del thread_id, command
        return self._result

    async def execute_command(
        self, intent: CommandIntent
    ) -> ApplicationResult[dict[str, object]]:
        del intent
        return self._result

    async def set_provider_credential(
        self, request: ProviderCredentialSetRequest
    ) -> ApplicationResult[dict[str, object]]:
        del request
        return self._result

    async def respond_interaction(
        self, interaction_id: str, decision: str
    ) -> ApplicationResult[dict[str, object]]:
        del interaction_id, decision
        return self._result

    async def cancel_operation(
        self, operation_id: str
    ) -> ApplicationResult[dict[str, object]]:
        del operation_id
        return self._result

    async def shutdown(self) -> ApplicationResult[dict[str, object]]:
        return self._result


def _cases(name: str) -> list[dict[str, Any]]:
    loaded = _load(name)
    assert isinstance(loaded, dict)
    cases = loaded.get("cases")
    assert isinstance(cases, list)
    assert all(isinstance(case, dict) for case in cases)
    return cases


@pytest.mark.asyncio
async def test_every_valid_method_fixture_round_trips_through_dispatcher() -> None:
    for index, case in enumerate(_cases("methods.valid.json"), start=1):
        expected = case["result"]
        assert isinstance(expected, dict)
        dispatcher = JsonRpcDispatcher(_FixtureFacade(expected))  # type: ignore[arg-type]

        response = await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": index,
                "method": case["method"],
                "params": case["params"],
            }
        )

        assert response is not None
        assert response.get("result") == expected, case["name"]
        encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
        assert len(encoded) < 1_048_576


@pytest.mark.asyncio
async def test_every_invalid_method_fixture_fails_at_declared_boundary() -> None:
    success = {"ok": True, "value": {}}
    for index, case in enumerate(_cases("methods.invalid.json"), start=1):
        dispatcher = JsonRpcDispatcher(_FixtureFacade(success))  # type: ignore[arg-type]
        response = await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": index,
                "method": case["method"],
                "params": case["params"],
            }
        )

        assert response is not None
        expected = case["expected"]
        if expected["kind"] == "jsonrpc_error":
            assert response["error"]["code"] == expected["code"], case["name"]
        else:
            assert response["result"]["ok"] is False, case["name"]
            assert response["result"]["error"]["code"] == expected["code"]


def test_every_event_fixture_matches_python_discriminated_union() -> None:
    valid = _load("events.valid.json")
    assert isinstance(valid, dict)
    events = valid["events"]
    assert len(events) == len(EventType)
    assert {event["event_type"] for event in events} == {
        event_type.value for event_type in EventType
    }
    for event in events:
        assert (
            EventEnvelope.model_validate(event).event_type.value == event["event_type"]
        )

    for case in _cases("events.invalid.json"):
        with pytest.raises(ValidationError) as raised:
            EventEnvelope.model_validate(case["event"])
        assert str(case["reason"]).casefold() in str(raised.value).casefold()


def test_failure_and_command_fixtures_are_complete_and_valid() -> None:
    failures = _cases("results.failures.json")
    assert {case["code"] for case in failures} == {
        code.value for code in ProductErrorCode
    }
    for case in failures:
        result = ApplicationResult[dict[str, object]].model_validate(case["result"])
        assert result.ok is False
        assert result.error is not None and result.error.code.value == case["code"]

    commands = _load("commands.json")
    assert isinstance(commands, dict)
    assert commands["commands"] == [
        {"name": name.value, "owner": COMMAND_OWNERS[name].value}
        for name in CommandName
    ]
