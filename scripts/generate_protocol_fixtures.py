from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from awesome_agent.application.commands import (
    COMMAND_OWNERS,
    CommandIntent,
    CommandName,
    CommandResult,
    CommandStatus,
)
from awesome_agent.application.contracts import (
    ApplicationResult,
    ApplicationState,
    CancelResult,
    InitializeResult,
    InitializeStatus,
    InteractionResult,
    OperationAccepted,
    ProductError,
    ProductErrorCode,
    ProviderCredentialSetResult,
    ProviderCredentialSetStatus,
    ShutdownResult,
    StatusSnapshot,
    ThreadListResult,
    ThreadReadResult,
    WorkspacePresentation,
)
from awesome_agent.config import CredentialSource, SecretStatus
from awesome_agent.conversation import Thread, ThreadView
from awesome_agent.core.events import (
    AssistantReasoningDeltaPayload,
    AssistantTextDeltaPayload,
    ContextPayload,
    EventEnvelope,
    EventPayload,
    EventType,
    InteractionChoicePayload,
    InteractionRequiredPayload,
    InteractionResolvedPayload,
    MemoryStatusPayload,
    OperationLifecyclePayload,
    ProviderRetryingPayload,
    ToolResultPayload,
    ToolStartedPayload,
    TurnLifecyclePayload,
    UsageUpdatedPayload,
    WarningPayload,
    WorkspaceChangedPayload,
)
from awesome_agent.modeling import ModelIdentitySnapshot
from awesome_agent.version import PRODUCT_VERSION

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "protocol" / "fixtures" / "v1"
FIXED_TIME = datetime(2026, 7, 11, 8, 0, tzinfo=UTC)
WORKSPACE_KEY = "ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
THREAD_ID = "thread_11111111111111111111111111111111"
TURN_ID = "turn_22222222222222222222222222222222"
OPERATION_ID = "operation_33333333333333333333333333333333"
CLIENT_MESSAGE_ID = "client_44444444444444444444444444444444"

METHODS = (
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
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _model(value: Any) -> Any:
    return value.model_dump(mode="json", exclude_none=True)


def _success(value: Any) -> Any:
    return _model(ApplicationResult.success(value))


def _thread() -> Thread:
    return Thread(
        id=THREAD_ID,
        workspace_key=WORKSPACE_KEY,
        title="Fixture Thread",
        current_model="deepseek/deepseek-v4-flash",
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
    )


def _valid_methods() -> dict[str, object]:
    workspace = WorkspacePresentation(
        display_path="C:\\workspace",
        branch="feature/fixtures",
    )
    status_snapshot = StatusSnapshot(
        version=PRODUCT_VERSION,
        workspace_path=workspace.display_path,
        thread_title="Fixture Thread",
        thread_id=THREAD_ID,
        thread_display_id="thread_11111111",
        model_identity=ModelIdentitySnapshot.from_models(
            configured_model="deepseek/deepseek-v4-flash",
            effective_model="deepseek/deepseek-v4-flash",
        ),
        model_status="configured",
        thinking_enabled=False,
        skill_mode="auto",
        local_memory_enabled=False,
        mem0_enabled=False,
        mcp_ready=0,
        mcp_degraded=0,
        operation_status="idle",
        operation_id=None,
        configuration_valid=True,
        configuration_diagnostic_count=0,
    )
    cases: tuple[tuple[str, str, dict[str, object], object], ...] = (
        (
            "initialize.ready",
            "initialize",
            {
                "protocol_version": 1,
                "client_name": "awesome",
                "client_version": PRODUCT_VERSION,
            },
            _success(
                InitializeResult(
                    product_version=PRODUCT_VERSION,
                    protocol_version=1,
                    status=InitializeStatus.READY,
                    session_id="session_11111111111111111111111111111111",
                    workspace=workspace,
                    capabilities=("threads", "turns", "commands"),
                )
            ),
        ),
        (
            "application.get_state",
            "application.getState",
            {},
            _success(
                ApplicationState(
                    initialized=True,
                    session_id="session_11111111111111111111111111111111",
                    workspace_key=WORKSPACE_KEY,
                    workspace=workspace,
                    workspace_trusted=True,
                    current_thread_id=THREAD_ID,
                    model_identity=ModelIdentitySnapshot.from_models(
                        configured_model="deepseek/deepseek-v4-flash",
                        effective_model="deepseek/deepseek-v4-flash",
                    ),
                    configuration_valid=True,
                    secret_status=SecretStatus(deepseek_api_key=True),
                    usage={"active_execution_seconds": 0.5},
                )
            ),
        ),
        (
            "thread.list",
            "thread.list",
            {"limit": 50},
            _success(ThreadListResult(threads=(_thread(),))),
        ),
        (
            "thread.read",
            "thread.read",
            {"thread_id": THREAD_ID, "limit": 50},
            _success(ThreadReadResult(view=ThreadView(thread=_thread()))),
        ),
        (
            "turn.submit",
            "turn.submit",
            {
                "thread_id": THREAD_ID,
                "content": "Inspect the repository.",
                "client_message_id": CLIENT_MESSAGE_ID,
            },
            _success(
                OperationAccepted(
                    operation_id=OPERATION_ID,
                    thread_id=THREAD_ID,
                    turn_id=TURN_ID,
                    client_message_id=CLIENT_MESSAGE_ID,
                )
            ),
        ),
        (
            "direct.execute",
            "direct.execute",
            {"thread_id": THREAD_ID, "command": "git status"},
            _success(
                OperationAccepted(
                    operation_id=OPERATION_ID,
                    thread_id=THREAD_ID,
                )
            ),
        ),
        (
            "command.execute",
            "command.execute",
            _model(CommandIntent(name=CommandName.STATUS)),
            _success(
                CommandResult(
                    status=CommandStatus.SUCCESS,
                    data=cast(dict[str, Any], status_snapshot.model_dump(mode="json")),
                )
            ),
        ),
        (
            "provider.credential.set",
            "provider.credential.set",
            {
                "provider": "deepseek",
                "action": "add",
                "api_key": "fixture-request-secret",
                "allow_unverified": False,
            },
            _success(
                ProviderCredentialSetResult(
                    provider="deepseek",
                    status=ProviderCredentialSetStatus.CONFIGURED,
                    source=CredentialSource.AWESOME,
                    code="credential_saved",
                )
            ),
        ),
        (
            "interaction.respond",
            "interaction.respond",
            {"interaction_id": "interaction_1", "decision": "trust"},
            _success(InteractionResult(accepted=True, status="resolved")),
        ),
        (
            "operation.cancel",
            "operation.cancel",
            {"operation_id": OPERATION_ID},
            _success(CancelResult(operation_id=OPERATION_ID, cancelled=True)),
        ),
        (
            "shutdown",
            "shutdown",
            {},
            _success(ShutdownResult()),
        ),
    )
    return {
        "cases": [
            {"name": name, "method": method, "params": params, "result": result}
            for name, method, params, result in cases
        ]
    }


def _invalid_methods() -> dict[str, object]:
    return {
        "cases": [
            {
                "name": "initialize.missing_client_version",
                "method": "initialize",
                "params": {"protocol_version": 1, "client_name": "awesome"},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "initialize.protocol_incompatible",
                "method": "initialize",
                "params": {
                    "protocol_version": 2,
                    "client_name": "awesome",
                    "client_version": PRODUCT_VERSION,
                },
                "expected": {
                    "kind": "product_error",
                    "code": "protocol_version_incompatible",
                },
            },
            {
                "name": "application.get_state.extra",
                "method": "application.getState",
                "params": {"extra": True},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "thread.list.limit",
                "method": "thread.list",
                "params": {"limit": 201},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "thread.read.before_sequence",
                "method": "thread.read",
                "params": {"thread_id": THREAD_ID, "before_sequence": 0},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "turn.submit.client_message_id_missing",
                "method": "turn.submit",
                "params": {"thread_id": THREAD_ID, "content": "Inspect."},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "turn.submit.empty",
                "method": "turn.submit",
                "params": {
                    "thread_id": THREAD_ID,
                    "content": "",
                    "client_message_id": CLIENT_MESSAGE_ID,
                },
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "direct.execute.empty",
                "method": "direct.execute",
                "params": {"thread_id": THREAD_ID, "command": ""},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "command.execute.unknown",
                "method": "command.execute",
                "params": {"name": "unknown"},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "interaction.respond.empty",
                "method": "interaction.respond",
                "params": {"interaction_id": "interaction_1", "decision": ""},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "operation.cancel.empty",
                "method": "operation.cancel",
                "params": {"operation_id": ""},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "shutdown.extra",
                "method": "shutdown",
                "params": {"force": True},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "method.unknown",
                "method": "debug.dump",
                "params": {},
                "expected": {"kind": "jsonrpc_error", "code": -32601},
            },
        ]
    }


def _failure_results() -> dict[str, object]:
    return {
        "cases": [
            {
                "code": code.value,
                "result": _model(
                    ApplicationResult[dict[str, object]].failure(
                        ProductError(
                            code=code,
                            message=f"Safe {code.value} message.",
                            retryable=code
                            in {
                                ProductErrorCode.OPERATION_BUSY,
                                ProductErrorCode.TURN_BUSY,
                            },
                        )
                    )
                ),
            }
            for code in ProductErrorCode
        ]
    }


def _payload(event_type: EventType) -> EventPayload:
    if event_type.value.startswith("operation."):
        return OperationLifecyclePayload(kind=cast(Any, event_type))
    if event_type.value.startswith("turn."):
        return TurnLifecyclePayload(
            kind=cast(Any, event_type),
            duration_ms=(None if event_type is EventType.TURN_STARTED else 1_250),
        )
    if event_type is EventType.ASSISTANT_TEXT_DELTA:
        return AssistantTextDeltaPayload(text="answer")
    if event_type is EventType.ASSISTANT_REASONING_DELTA:
        return AssistantReasoningDeltaPayload(text="reasoning")
    if event_type is EventType.PROVIDER_RETRYING:
        return ProviderRetryingPayload(
            attempt=2,
            maximum=6,
            delay_seconds=0.5,
            error_code="provider_unavailable",
        )
    if event_type is EventType.TOOL_STARTED:
        return ToolStartedPayload(
            call_id="call_1",
            tool_name="read_file",
            verb="Read",
            target="src/example.py",
        )
    if event_type in {
        EventType.TOOL_COMPLETED,
        EventType.TOOL_FAILED,
        EventType.TOOL_CANCELLED,
    }:
        return ToolResultPayload(
            kind=cast(Any, event_type),
            call_id="call_1",
            tool_name="read_file",
            verb="Read",
            target="src/example.py",
            outcome=("Read" if event_type is EventType.TOOL_COMPLETED else "Failed"),
            summary="Safe tool summary.",
            detail="Safe bounded detail.",
            duration_ms=18,
        )
    if event_type in {EventType.CONTEXT_PREPARED, EventType.CONTEXT_COMPRESSED}:
        return ContextPayload(
            kind=cast(Any, event_type),
            source_count=2,
            estimated_tokens=128,
        )
    if event_type is EventType.USAGE_UPDATED:
        return UsageUpdatedPayload(input_tokens=12, output_tokens=4)
    if event_type is EventType.WORKSPACE_CHANGED:
        return WorkspaceChangedPayload(
            change_set_id="change_1",
            paths=("src/example.py",),
            reversibility="full",
        )
    if event_type is EventType.MEMORY_STATUS:
        return MemoryStatusPayload(layer="local", enabled=False, status="disabled")
    if event_type is EventType.INTERACTION_REQUIRED:
        return InteractionRequiredPayload(
            interaction_id="interaction_1",
            interaction_kind="workspace_trust",
            prompt="Trust this workspace?",
            operation="trust",
            target="C:\\workspace",
            capability=None,
            choices=(
                InteractionChoicePayload(decision="trust", label="Yes"),
                InteractionChoicePayload(decision="deny", label="No"),
            ),
        )
    if event_type is EventType.INTERACTION_RESOLVED:
        return InteractionResolvedPayload(
            interaction_id="interaction_1",
            decision="trust",
        )
    if event_type is EventType.WARNING:
        return WarningPayload(code="safe_warning", message="Safe warning.")
    raise AssertionError(f"Missing fixture payload for {event_type.value}")


def _event(event_type: EventType, sequence: int) -> EventEnvelope:
    operation_id = OPERATION_ID if event_type.value.startswith("operation.") else None
    thread_id = THREAD_ID if event_type.value.startswith("turn.") else None
    turn_id = TURN_ID if event_type.value.startswith("turn.") else None
    return EventEnvelope(
        event_id=f"event_{sequence:03d}",
        sequence=sequence,
        session_id="session_11111111111111111111111111111111",
        workspace_key=WORKSPACE_KEY,
        thread_id=thread_id,
        turn_id=turn_id,
        operation_id=operation_id,
        client_message_id=CLIENT_MESSAGE_ID if turn_id is not None else None,
        event_type=event_type,
        timestamp=FIXED_TIME,
        payload=_payload(event_type),
    )


def _valid_events() -> dict[str, object]:
    return {
        "events": [
            _model(_event(event_type, index))
            for index, event_type in enumerate(EventType, start=1)
        ]
    }


def _invalid_events() -> dict[str, object]:
    valid = _model(_event(EventType.WARNING, 1))
    assert isinstance(valid, dict)
    operation = _model(_event(EventType.OPERATION_STARTED, 2))
    assert isinstance(operation, dict)
    return {
        "cases": [
            {
                "name": "sequence.zero",
                "event": {**valid, "sequence": 0},
                "reason": "sequence",
            },
            {
                "name": "event_type.payload_mismatch",
                "event": {**valid, "event_type": "assistant.text.delta"},
                "reason": "event_type must match",
            },
            {
                "name": "timestamp.non_utc",
                "event": {
                    **valid,
                    "timestamp": FIXED_TIME.astimezone(
                        timezone(timedelta(hours=8))
                    ).isoformat(),
                },
                "reason": "timestamp",
            },
            {
                "name": "operation.missing_identity",
                "event": {
                    **operation,
                    "operation_id": None,
                },
                "reason": "operation_id",
            },
            {
                "name": "unknown.field",
                "event": {**valid, "traceback": "private"},
                "reason": "extra",
            },
        ]
    }


def _commands() -> dict[str, object]:
    return {
        "commands": [
            {"name": name.value, "owner": COMMAND_OWNERS[name].value}
            for name in CommandName
        ]
    }


def build_files() -> dict[str, bytes]:
    files = {
        "commands.json": _json_bytes(_commands()),
        "events.invalid.json": _json_bytes(_invalid_events()),
        "events.valid.json": _json_bytes(_valid_events()),
        "methods.invalid.json": _json_bytes(_invalid_methods()),
        "methods.valid.json": _json_bytes(_valid_methods()),
        "results.failures.json": _json_bytes(_failure_results()),
    }
    manifest = {
        "fixture_version": 1,
        "product_version": PRODUCT_VERSION,
        "protocol_version": 1,
        "methods": list(METHODS),
        "event_types": [event_type.value for event_type in EventType],
        "command_owners": {
            name.value: COMMAND_OWNERS[name].value for name in CommandName
        },
        "files": {
            name: hashlib.sha256(content).hexdigest()
            for name, content in sorted(files.items())
        },
    }
    return {**files, "manifest.json": _json_bytes(manifest)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = build_files()
    if args.check:
        mismatches = [
            name
            for name, expected in files.items()
            if not (TARGET / name).is_file() or (TARGET / name).read_bytes() != expected
        ]
        if mismatches:
            print(
                "Protocol fixtures are stale: " + ", ".join(sorted(mismatches)),
                file=sys.stderr,
            )
            return 1
        return 0
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (TARGET / name).write_bytes(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
