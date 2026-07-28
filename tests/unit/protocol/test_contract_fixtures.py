from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from awesome_agent.application.bootstrap import BootstrapRejection
from awesome_agent.application.command_results import (
    COMMAND_OUTCOME_ADAPTER,
    CommandOutcome,
)
from awesome_agent.application.commands import (
    COMMAND_OWNERS,
    CommandIntent,
    CommandName,
)
from awesome_agent.application.contracts import (
    PROTOCOL_VERSION,
    ApplicationResult,
    ApplicationState,
    CancelResult,
    InitializeResult,
    InteractionResult,
    OperationAccepted,
    ProductErrorCode,
    ProviderCredentialSetRequest,
    ProviderCredentialSetResult,
    ShutdownResult,
    SkillInstallRequest,
    SkillInstallResult,
    SkillListResult,
    SkillRemoveRequest,
    SkillRemoveResult,
    ThreadListQuery,
    ThreadListResult,
    ThreadReadQuery,
    ThreadReadResult,
    ThreadSearchQuery,
)
from awesome_agent.application.middleware import ApplicationOperation
from awesome_agent.core.events import EventEnvelope, EventType
from awesome_agent.protocol.jsonrpc import JsonRpcDispatcher
from awesome_agent.version import PRODUCT_VERSION

ROOT = Path(__file__).parents[3]
FIXTURES = ROOT / "protocol" / "fixtures" / f"v{PROTOCOL_VERSION}"


def _load(name: str) -> dict[str, Any]:
    loaded: object = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_manifest_freezes_complete_protocol_inventory_and_hashes() -> None:
    manifest = _load("manifest.json")

    assert manifest["fixture_version"] == 1
    assert manifest["product_version"] == PRODUCT_VERSION
    assert manifest["protocol_version"] == PROTOCOL_VERSION
    assert {"command-results.valid.json", "command-results.invalid.json"} <= set(
        manifest["files"]
    )
    assert set(manifest["methods"]) == {
        "initialize",
        "skill.list",
        "skill.install",
        "skill.remove",
        "application.getState",
        "thread.list",
        "thread.search",
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
    def bootstrap_rejection(
        self,
        operation: ApplicationOperation | None,
    ) -> BootstrapRejection | None:
        del operation
        return None

    def __init__(self, result: dict[str, object]) -> None:
        self._result = ApplicationResult[dict[str, object]].model_validate(result)

    async def initialize(self) -> ApplicationResult[InitializeResult]:
        return ApplicationResult[InitializeResult].model_validate(self._result)

    async def get_state(self) -> ApplicationResult[ApplicationState]:
        return ApplicationResult[ApplicationState].model_validate(self._result)

    async def list_skills(self) -> ApplicationResult[SkillListResult]:
        return ApplicationResult[SkillListResult].model_validate(self._result)

    async def install_skill(
        self,
        request: SkillInstallRequest,
    ) -> ApplicationResult[SkillInstallResult]:
        del request
        return ApplicationResult[SkillInstallResult].model_validate(self._result)

    async def remove_skill(
        self,
        request: SkillRemoveRequest,
    ) -> ApplicationResult[SkillRemoveResult]:
        del request
        return ApplicationResult[SkillRemoveResult].model_validate(self._result)

    async def list_threads(
        self, query: ThreadListQuery
    ) -> ApplicationResult[ThreadListResult]:
        del query
        return ApplicationResult[ThreadListResult].model_validate(self._result)

    async def search_threads(
        self, query: ThreadSearchQuery
    ) -> ApplicationResult[ThreadListResult]:
        del query
        return ApplicationResult[ThreadListResult].model_validate(self._result)

    async def read_thread(
        self, query: ThreadReadQuery
    ) -> ApplicationResult[ThreadReadResult]:
        del query
        return ApplicationResult[ThreadReadResult].model_validate(self._result)

    async def submit_turn(
        self, thread_id: str, content: str, client_message_id: str
    ) -> ApplicationResult[OperationAccepted]:
        del thread_id, content, client_message_id
        return ApplicationResult[OperationAccepted].model_validate(self._result)

    async def execute_direct(
        self, thread_id: str, command: str
    ) -> ApplicationResult[OperationAccepted]:
        del thread_id, command
        return ApplicationResult[OperationAccepted].model_validate(self._result)

    async def execute_command(
        self, intent: CommandIntent
    ) -> ApplicationResult[CommandOutcome]:
        del intent
        return ApplicationResult[CommandOutcome].model_validate(self._result)

    async def set_provider_credential(
        self, request: ProviderCredentialSetRequest
    ) -> ApplicationResult[ProviderCredentialSetResult]:
        del request
        payload = self._result.model_dump(mode="json", exclude_none=True)
        value = payload.get("value")
        if payload.get("ok") is True and isinstance(value, dict):
            payload["value"] = {"source": None, **value}
        return ApplicationResult[ProviderCredentialSetResult].model_validate(payload)

    async def respond_interaction(
        self, interaction_id: str, decision: str
    ) -> ApplicationResult[InteractionResult]:
        del interaction_id, decision
        return ApplicationResult[InteractionResult].model_validate(self._result)

    async def cancel_operation(
        self, operation_id: str
    ) -> ApplicationResult[CancelResult]:
        del operation_id
        return ApplicationResult[CancelResult].model_validate(self._result)

    async def shutdown(self) -> ApplicationResult[ShutdownResult]:
        return ApplicationResult[ShutdownResult].model_validate(self._result)


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
        dispatcher = JsonRpcDispatcher(_FixtureFacade(expected))

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


def test_provider_credential_fixtures_freeze_source_omission_contract() -> None:
    credential_cases = {
        case["name"]: case
        for case in _cases("methods.valid.json")
        if case["method"] == "provider.credential.set"
    }

    assert set(credential_cases) == {
        "provider.credential.set.configured",
        "provider.credential.set.invalid",
        "provider.credential.set.confirm_unverified",
        "provider.credential.set.deleted",
    }
    configured = credential_cases["provider.credential.set.configured"]["result"][
        "value"
    ]
    assert configured["provider"] == "tavily"
    assert configured["source"] == "awesome"
    for name in {
        "provider.credential.set.invalid",
        "provider.credential.set.confirm_unverified",
        "provider.credential.set.deleted",
    }:
        value = credential_cases[name]["result"]["value"]
        assert "source" not in value


def test_skill_management_fixtures_expose_only_bounded_public_results() -> None:
    cases = {
        case["name"]: case
        for case in _cases("methods.valid.json")
        if str(case["method"]).startswith("skill.")
    }

    assert set(cases) == {
        "skill.list",
        "skill.install.installed",
        "skill.install.replaced",
        "skill.remove",
    }
    installed = cases["skill.install.installed"]["result"]["value"]
    replaced = cases["skill.install.replaced"]["result"]["value"]
    removed = cases["skill.remove"]["result"]["value"]
    assert installed == {"name": "review", "status": "installed"}
    assert replaced == {"name": "review", "status": "replaced"}
    assert removed == {"name": "review", "status": "removed"}
    encoded = json.dumps((installed, replaced, removed))
    assert "source_path" not in encoded
    assert "restart_required" not in encoded


def test_thread_read_fixture_contains_discriminated_change_deltas() -> None:
    case = next(
        item for item in _cases("methods.valid.json") if item["name"] == "thread.read"
    )
    result = ThreadReadResult.model_validate(case["result"]["value"])

    assert [change.kind for change in result.change_sets[0].changes] == [
        "text_file",
        "binary_file",
        "directory",
        "symlink",
    ]
    assistant = next(
        entry for entry in result.view.entries if entry.kind == "assistant_message"
    )
    assert assistant.metadata == {
        "citations": [
            {
                "id": "S1",
                "title": "Fixture source",
                "url": "https://example.com/source",
            }
        ]
    }
    assert result.view.turns[0].budgets.web_requests == 8
    assert result.view.turns[0].usage.web_requests == 1


@pytest.mark.asyncio
async def test_every_invalid_method_fixture_fails_at_declared_boundary() -> None:
    success = {"ok": True, "value": {}}
    for index, case in enumerate(_cases("methods.invalid.json"), start=1):
        dispatcher = JsonRpcDispatcher(_FixtureFacade(success))
        response = await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": case.get("request_id", index),
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
        if result.error.code is ProductErrorCode.STATE_CREATED_BY_NEWER_VERSION:
            assert result.error.data == {
                "found_schema": 8,
                "expected_schema": 7,
                "state_directory": "C:\\Awesome\\state",
            }

    commands = _load("commands.json")
    assert isinstance(commands, dict)
    assert commands["commands"] == [
        {"name": name.value, "owner": COMMAND_OWNERS[name].value}
        for name in CommandName
    ]


def test_command_outcome_corpus_is_complete_and_strict() -> None:
    valid = _cases("command-results.valid.json")
    assert {
        case["outcome"]["payload"]["kind"]
        for case in valid
        if case["outcome"]["kind"] == "result"
    } == {
        "notice",
        "thread_transition",
        "thread_retry",
        "thread_renamed",
        "context",
        "compact",
        "model",
        "thinking",
        "workspace",
        "thread_export",
        "diff",
        "change",
        "tools",
        "web_status",
        "skills",
        "mcp",
        "memory_status",
        "memory_document",
        "memory_search",
        "memory_mutation",
        "status",
        "usage",
        "doctor",
        "config",
        "permissions",
    }
    for case in valid:
        COMMAND_OUTCOME_ADAPTER.validate_python(case["outcome"])
    invalid = _cases("command-results.invalid.json")
    assert {
        "thread_retry_operation_client_message_mismatch",
        "thread_retry_operation_user_entry_missing",
        "thread_retry_operation_turn_terminal",
        "thread_retry_operation_turn_not_last",
        "thread_retry_operation_multiple_in_progress",
        "web_status_empty_diagnostic_code",
        "web_status_invalid_diagnostic_code",
        "tools_duplicate_available_name",
        "tools_duplicate_unavailable_name",
        "tools_available_unavailable_overlap",
    }.issubset({case["name"] for case in invalid})
    for case in invalid:
        with pytest.raises(ValidationError):
            COMMAND_OUTCOME_ADAPTER.validate_python(case["outcome"])
