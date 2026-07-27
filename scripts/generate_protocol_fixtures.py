from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from awesome_agent.application.command_results import (
    COMMAND_OUTCOME_ADAPTER,
    StatusCommandPayload,
    WebStatusCommandPayload,
    result,
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
    ChangeSetSummary,
    InitializeResult,
    InitializeStatus,
    InteractionResult,
    OperationAccepted,
    ProductError,
    ProductErrorCode,
    ProviderCredentialSetResult,
    ProviderCredentialSetStatus,
    ShutdownResult,
    SkillInstallResult,
    SkillListResult,
    SkillPackageSummary,
    SkillRemoveResult,
    StatusSnapshot,
    ThreadListResult,
    ThreadReadResult,
    WorkspacePresentation,
)
from awesome_agent.application.web_commands import TAVILY_DISCLOSURE
from awesome_agent.config import (
    BudgetConfig,
    CredentialSource,
    SecretStatus,
    missing_provider_credential_statuses,
)
from awesome_agent.context import (
    WorkspaceInstructionDiagnostic,
    WorkspaceInstructionDiagnosticCode,
)
from awesome_agent.conversation import (
    Thread,
    ThreadEntry,
    ThreadEntryKind,
    ThreadLineage,
    ThreadTitleSource,
    ThreadView,
    Turn,
    TurnStatus,
    UsageSummary,
)
from awesome_agent.core.changes import (
    BinaryFileChange,
    DirectoryChange,
    FileChangeKind,
    SymlinkChange,
    TextFileChange,
)
from awesome_agent.core.citations import Citation
from awesome_agent.core.contracts import MAX_JSON_SAFE_INTEGER
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
)
from awesome_agent.core.tools.permissions import PermissionMode
from awesome_agent.modeling import MODEL_CATALOG, ModelIdentitySnapshot
from awesome_agent.version import PRODUCT_VERSION

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "protocol" / "fixtures" / f"v{PROTOCOL_VERSION}"
FIXED_TIME = datetime(2026, 7, 11, 8, 0, tzinfo=UTC)
WORKSPACE_KEY = "ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
THREAD_ID = "thread_11111111111111111111111111111111"
TURN_ID = "turn_22222222222222222222222222222222"
OPERATION_ID = "operation_33333333333333333333333333333333"
CLIENT_MESSAGE_ID = "client_44444444444444444444444444444444"
RETRY_THREAD_ID = "thread_77777777777777777777777777777777"
RETRY_TURN_ID = "turn_88888888888888888888888888888888"
RETRY_OPERATION_ID = "operation_99999999999999999999999999999999"
RETRY_CLIENT_MESSAGE_ID = "client_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
FORK_THREAD_ID = "thread_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CAPABILITIES = ("threads", "turns", "commands", "web", "citations")

METHODS = (
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
)


def _json_bytes(value: object, *, ensure_ascii: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=ensure_ascii,
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


def _thread_view() -> ThreadView:
    user_entry_id = "entry_55555555555555555555555555555555"
    assistant_entry_id = "entry_66666666666666666666666666666666"
    return ThreadView(
        thread=_thread(),
        entries=(
            ThreadEntry(
                id=user_entry_id,
                thread_id=THREAD_ID,
                sequence=1,
                kind=ThreadEntryKind.USER_MESSAGE,
                content="Inspect the repository.",
                client_message_id=CLIENT_MESSAGE_ID,
                created_at=FIXED_TIME,
            ),
            ThreadEntry(
                id=assistant_entry_id,
                thread_id=THREAD_ID,
                sequence=2,
                kind=ThreadEntryKind.ASSISTANT_MESSAGE,
                content="The source confirms the result. [[S1]]",
                metadata={
                    "citations": [
                        Citation(
                            id="S1",
                            title="Fixture source",
                            url="https://example.com/source",
                        ).model_dump(mode="json")
                    ]
                },
                created_at=FIXED_TIME,
            ),
        ),
        turns=(
            Turn(
                id=TURN_ID,
                thread_id=THREAD_ID,
                checkpoint_key=TURN_ID,
                status=TurnStatus.COMPLETED,
                provider="deepseek",
                model="deepseek-chat",
                thinking_enabled=True,
                skill_mode="auto",
                budgets=BudgetConfig(web_requests=8),
                user_entry_id=user_entry_id,
                assistant_entry_id=assistant_entry_id,
                usage=UsageSummary(model_calls=1, web_requests=1),
                termination_reason="stop",
                created_at=FIXED_TIME,
                updated_at=FIXED_TIME,
                completed_at=FIXED_TIME,
            ),
        ),
    )


def _retry_thread_read() -> ThreadReadResult:
    user_entry_id = "entry_77777777777777777777777777777777"
    thread = Thread(
        id=RETRY_THREAD_ID,
        workspace_key=WORKSPACE_KEY,
        title="Retry Fixture Thread",
        current_model="deepseek/deepseek-v4-flash",
        lineage=ThreadLineage(
            kind="retry",
            source_thread_id=THREAD_ID,
            source_turn_id=TURN_ID,
        ),
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
    )
    user_entry = ThreadEntry(
        id=user_entry_id,
        thread_id=RETRY_THREAD_ID,
        sequence=1,
        kind=ThreadEntryKind.USER_MESSAGE,
        content="Inspect the repository.",
        client_message_id=RETRY_CLIENT_MESSAGE_ID,
        created_at=FIXED_TIME,
    )
    turn = Turn(
        id=RETRY_TURN_ID,
        thread_id=RETRY_THREAD_ID,
        checkpoint_key=RETRY_TURN_ID,
        status=TurnStatus.IN_PROGRESS,
        provider="deepseek",
        model="deepseek-chat",
        thinking_enabled=True,
        skill_mode="auto",
        budgets=BudgetConfig(web_requests=8),
        user_entry_id=user_entry_id,
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
    )
    return ThreadReadResult(
        view=ThreadView(thread=thread, entries=(user_entry,), turns=(turn,))
    )


def _fork_thread_read() -> ThreadReadResult:
    return ThreadReadResult(
        view=ThreadView(
            thread=Thread(
                id=FORK_THREAD_ID,
                workspace_key=WORKSPACE_KEY,
                title="Fork Fixture Thread",
                current_model="deepseek/deepseek-v4-flash",
                lineage=ThreadLineage(
                    kind="fork",
                    source_thread_id=THREAD_ID,
                    source_turn_id=TURN_ID,
                ),
                created_at=FIXED_TIME,
                updated_at=FIXED_TIME,
            )
        )
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
        thinking_enabled=True,
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
                "protocol_version": PROTOCOL_VERSION,
                "client_name": "awesome",
                "client_version": PRODUCT_VERSION,
            },
            _success(
                InitializeResult(
                    product_version=PRODUCT_VERSION,
                    protocol_version=PROTOCOL_VERSION,
                    status=InitializeStatus.READY,
                    session_id="session_11111111111111111111111111111111",
                    workspace=workspace,
                    capabilities=CAPABILITIES,
                )
            ),
        ),
        (
            "initialize.state_reset_required",
            "initialize",
            {
                "protocol_version": PROTOCOL_VERSION,
                "client_name": "awesome",
                "client_version": PRODUCT_VERSION,
            },
            _success(
                InitializeResult(
                    product_version=PRODUCT_VERSION,
                    protocol_version=PROTOCOL_VERSION,
                    status=InitializeStatus.STATE_RESET_REQUIRED,
                    session_id="session_11111111111111111111111111111111",
                    interaction_id="interaction_state_reset",
                    workspace=WorkspacePresentation(display_path="C:\\workspace"),
                    capabilities=CAPABILITIES,
                )
            ),
        ),
        (
            "skill.list",
            "skill.list",
            {},
            _success(
                SkillListResult(
                    skills=(
                        SkillPackageSummary(
                            name="review",
                            description="Review code safely",
                        ),
                    )
                )
            ),
        ),
        (
            "skill.install.installed",
            "skill.install",
            {"source_path": "C:\\packages\\review", "replace": False},
            _success(SkillInstallResult(name="review", status="installed")),
        ),
        (
            "skill.install.replaced",
            "skill.install",
            {"source_path": "C:\\packages\\review.zip", "replace": True},
            _success(SkillInstallResult(name="review", status="replaced")),
        ),
        (
            "skill.remove",
            "skill.remove",
            {"name": "review"},
            _success(SkillRemoveResult(name="review", status="removed")),
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
                    model_catalog=MODEL_CATALOG,
                    model_identity=ModelIdentitySnapshot.from_models(
                        configured_model="deepseek/deepseek-v4-flash",
                        effective_model="deepseek/deepseek-v4-flash",
                    ),
                    configuration_valid=True,
                    permission_mode=PermissionMode.ACCEPT_EDITS,
                    secret_status=SecretStatus(deepseek_api_key=True),
                    usage={"active_execution_seconds": 0.5},
                    workspace_instruction_diagnostic=(
                        WorkspaceInstructionDiagnostic(
                            code=WorkspaceInstructionDiagnosticCode.TOO_LARGE,
                            message=(
                                "AGENTS.md was ignored because it exceeds the "
                                "32 KiB limit."
                            ),
                        )
                    ),
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
            "thread.search",
            "thread.search",
            {"query": "  fixture source  ", "limit": 50},
            _success(ThreadListResult(threads=(_thread(),))),
        ),
        (
            "thread.read",
            "thread.read",
            {"thread_id": THREAD_ID, "limit": 50},
            _success(
                ThreadReadResult(
                    view=_thread_view(),
                    change_sets=(
                        ChangeSetSummary(
                            change_set_id="change_11111111111111111111111111111111",
                            turn_id=TURN_ID,
                            operation_id=OPERATION_ID,
                            lifecycle="applied",
                            changes=(
                                TextFileChange(
                                    path="src/main.py",
                                    change_kind=FileChangeKind.UPDATED,
                                    additions=16,
                                    deletions=2,
                                ),
                                BinaryFileChange(
                                    path="assets/logo.bin",
                                    change_kind=FileChangeKind.UPDATED,
                                    before_bytes=12,
                                    after_bytes=20,
                                ),
                                DirectoryChange(
                                    path="generated",
                                    change_kind=FileChangeKind.CREATED,
                                ),
                                SymlinkChange(
                                    path="current",
                                    change_kind=FileChangeKind.UPDATED,
                                ),
                            ),
                            created_at=FIXED_TIME,
                            sealed_at=FIXED_TIME,
                        ),
                    ),
                )
            ),
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
            _success(result(StatusCommandPayload(snapshot=status_snapshot))),
        ),
        (
            "provider.credential.set.configured",
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
            "provider.credential.set.invalid",
            "provider.credential.set",
            {
                "provider": "deepseek",
                "action": "add",
                "api_key": "fixture-invalid-secret",
                "allow_unverified": False,
            },
            _success(
                ProviderCredentialSetResult(
                    provider="deepseek",
                    status=ProviderCredentialSetStatus.INVALID,
                    source=None,
                    code="credential_invalid",
                )
            ),
        ),
        (
            "provider.credential.set.confirm_unverified",
            "provider.credential.set",
            {
                "provider": "kimi",
                "action": "add",
                "api_key": "fixture-unverified-secret",
                "allow_unverified": False,
            },
            _success(
                ProviderCredentialSetResult(
                    provider="kimi",
                    status=ProviderCredentialSetStatus.CONFIRM_UNVERIFIED,
                    source=None,
                    code="credential_validation_unavailable",
                )
            ),
        ),
        (
            "provider.credential.set.deleted",
            "provider.credential.set",
            {"provider": "mem0", "action": "delete"},
            _success(
                ProviderCredentialSetResult(
                    provider="mem0",
                    status=ProviderCredentialSetStatus.DELETED,
                    source=None,
                    code="credential_deleted",
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
            "interaction.respond.state_reset",
            "interaction.respond",
            {"interaction_id": "interaction_state_reset", "decision": "reset_state"},
            _success(InteractionResult(accepted=True, status="resolved")),
        ),
        (
            "interaction.respond.state_reset_denied",
            "interaction.respond",
            {"interaction_id": "interaction_state_reset", "decision": "deny"},
            _success(InteractionResult(accepted=True, status="denied")),
        ),
        (
            "interaction.respond.recovery_retry",
            "interaction.respond",
            {"interaction_id": "interaction_recovery", "decision": "retry"},
            _success(InteractionResult(accepted=True, status="resolved")),
        ),
        (
            "interaction.respond.recovery_abort",
            "interaction.respond",
            {"interaction_id": "interaction_recovery", "decision": "abort"},
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
                "params": {
                    "protocol_version": PROTOCOL_VERSION,
                    "client_name": "awesome",
                },
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "initialize.protocol_incompatible",
                "method": "initialize",
                "params": {
                    "protocol_version": 3,
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
                "name": "skill.list.extra",
                "method": "skill.list",
                "params": {"extra": True},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "skill.install.missing_source",
                "method": "skill.install",
                "params": {},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "skill.install.replace_type",
                "method": "skill.install",
                "params": {"source_path": "review", "replace": 1},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "skill.install.extra",
                "method": "skill.install",
                "params": {"source_path": "review", "extra": True},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "skill.remove.invalid_name",
                "method": "skill.remove",
                "params": {"name": "../review"},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "skill.remove.extra",
                "method": "skill.remove",
                "params": {"name": "review", "extra": True},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "request_id.unpaired_surrogate",
                "request_id": "\ud800",
                "method": "application.getState",
                "params": {},
                "expected": {"kind": "jsonrpc_error", "code": -32600},
            },
            {
                "name": "thread.list.limit",
                "method": "thread.list",
                "params": {"limit": 201},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "thread.list.limit_type",
                "method": "thread.list",
                "params": {"limit": "50"},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "thread.list.explicit_null",
                "method": "thread.list",
                "params": {"cursor": None},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "thread.search.blank_query",
                "method": "thread.search",
                "params": {"query": "   "},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "thread.search.limit",
                "method": "thread.search",
                "params": {"query": "fixture", "limit": 51},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "thread.search.explicit_null",
                "method": "thread.search",
                "params": {"query": "fixture", "cursor": None},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "thread.read.before_sequence",
                "method": "thread.read",
                "params": {"thread_id": THREAD_ID, "before_sequence": 0},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "thread.read.before_sequence_type",
                "method": "thread.read",
                "params": {"thread_id": THREAD_ID, "before_sequence": True},
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "thread.read.before_sequence_unsafe",
                "method": "thread.read",
                "params": {
                    "thread_id": THREAD_ID,
                    "before_sequence": 9_007_199_254_740_992,
                },
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
                "name": "provider.credential.allow_unverified_type",
                "method": "provider.credential.set",
                "params": {
                    "provider": "deepseek",
                    "action": "add",
                    "api_key": "fixture-request-secret",
                    "allow_unverified": "true",
                },
                "expected": {"kind": "jsonrpc_error", "code": -32602},
            },
            {
                "name": "provider.credential.explicit_null",
                "method": "provider.credential.set",
                "params": {
                    "provider": "deepseek",
                    "action": "delete",
                    "api_key": None,
                },
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
    cases: list[dict[str, object]] = []
    for code in ProductErrorCode:
        data: dict[str, Any] = {}
        if code is ProductErrorCode.STATE_CREATED_BY_NEWER_VERSION:
            data = {
                "found_schema": 8,
                "expected_schema": 7,
                "state_directory": "C:\\Awesome\\state",
            }
        elif code in {
            ProductErrorCode.STATE_UNKNOWN,
            ProductErrorCode.STATE_UNAVAILABLE,
            ProductErrorCode.STATE_RESET_BUSY,
        }:
            data = {"state_directory": "C:\\Awesome\\state"}
        elif code is ProductErrorCode.STATE_RESET_FAILED:
            data = {
                "diagnostic_code": "fresh_state_initialization_failed",
                "state_directory": "C:\\Awesome\\state",
            }
        cases.append(
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
                                ProductErrorCode.STATE_UNAVAILABLE,
                                ProductErrorCode.STATE_RESET_BUSY,
                                ProductErrorCode.STATE_RESET_FAILED,
                            },
                            data=data,
                        )
                    )
                ),
            }
        )
    return {"cases": cases}


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
        return UsageUpdatedPayload(
            input_tokens=MAX_JSON_SAFE_INTEGER,
            output_tokens=4,
        )
    if event_type is EventType.MEMORY_STATUS:
        return MemoryStatusPayload(layer="local", enabled=False, status="disabled")
    if event_type is EventType.INTERACTION_REQUIRED:
        return InteractionRequiredPayload(
            interaction_id="interaction_state_reset",
            interaction_kind="state_reset",
            prompt="Awesome needs to reset local state",
            operation="reset_local_state",
            target="local state",
            capability=None,
            choices=(
                InteractionChoicePayload(
                    decision="reset_state",
                    label="Reset local state and continue",
                ),
                InteractionChoicePayload(decision="deny", label="Exit"),
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
    usage = _model(_event(EventType.USAGE_UPDATED, 3))
    assert isinstance(usage, dict)
    usage_payload = usage["payload"]
    assert isinstance(usage_payload, dict)
    return {
        "cases": [
            {
                "name": "sequence.zero",
                "event": {**valid, "sequence": 0},
                "reason": "sequence",
            },
            {
                "name": "sequence.unsafe_integer",
                "event": {
                    **valid,
                    "sequence": MAX_JSON_SAFE_INTEGER + 1,
                },
                "reason": "safe range",
            },
            {
                "name": "usage.unsafe_integer",
                "event": {
                    **usage,
                    "payload": {
                        **usage_payload,
                        "input_tokens": MAX_JSON_SAFE_INTEGER + 1,
                    },
                },
                "reason": "safe range",
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


def _valid_command_results() -> dict[str, object]:
    methods = cast(list[dict[str, Any]], _valid_methods()["cases"])
    status = next(
        case["result"]["value"]["payload"]["snapshot"]
        for case in methods
        if case["name"] == "command.execute"
    )
    application = next(
        case["result"]["value"]
        for case in methods
        if case["name"] == "application.get_state"
    )
    thread = next(
        case["result"]["value"] for case in methods if case["name"] == "thread.read"
    )
    retry_thread = _model(_retry_thread_read())
    retry_application = {
        **application,
        "current_thread_id": RETRY_THREAD_ID,
        "active_operation_id": RETRY_OPERATION_ID,
    }
    fork_thread = _model(_fork_thread_read())
    fork_application = {
        **application,
        "current_thread_id": FORK_THREAD_ID,
        "active_operation_id": None,
    }
    fork_payload = {
        "kind": "thread_transition",
        "transition": {
            "reason": "fork",
            "application": fork_application,
            "thread": fork_thread,
        },
    }
    credentials = _model(missing_provider_credential_statuses())
    usage = _model(UsageSummary(active_execution_seconds=0.5))
    payloads: list[dict[str, object]] = [
        {"kind": "notice", "message": "Ready"},
        {
            "kind": "thread_transition",
            "transition": {
                "reason": "new",
                "application": application,
                "thread": thread,
            },
        },
        {
            "kind": "thread_retry",
            "transition": {
                "reason": "retry",
                "application": retry_application,
                "thread": retry_thread,
            },
            "operation": {
                "operation_id": RETRY_OPERATION_ID,
                "thread_id": RETRY_THREAD_ID,
                "turn_id": RETRY_TURN_ID,
                "client_message_id": RETRY_CLIENT_MESSAGE_ID,
            },
        },
        {
            "kind": "thread_renamed",
            "thread": _model(
                _thread().model_copy(
                    update={
                        "title": "Renamed Thread",
                        "title_source": ThreadTitleSource.MANUAL,
                    }
                )
            ),
        },
        {
            "kind": "context",
            "categories": [
                {"name": name, "estimated_tokens": value}
                for name, value in (
                    ("instructions", 10),
                    ("conversation", 20),
                    ("files", 30),
                    ("memory", 40),
                )
            ],
            "total_tokens": 100,
            "budget_tokens": 262144,
        },
        {
            "kind": "compact",
            "old_covered_entry_sequence": 0,
            "new_covered_entry_sequence": 4,
            "usage": usage,
        },
        {
            "kind": "model",
            "model": "deepseek/deepseek-v4-flash",
            "default_model_updated": True,
        },
        {"kind": "thinking", "enabled": False},
        {"kind": "workspace", "path": "C:\\workspace"},
        {
            "kind": "thread_export",
            "thread_id": THREAD_ID,
            "path": "exports/fixture.md",
            "format": "markdown",
            "write_status": "created",
            "byte_count": 1_024,
            "change_set_id": "change_11111111111111111111111111111111",
        },
        {"kind": "diff", "change_set_id": None, "content": ""},
        {
            "kind": "change",
            "action": "undo",
            "change_set_id": "change_1",
            "lifecycle": "undone",
            "restored_paths": ["src/example.py"],
            "warning": None,
        },
        {
            "kind": "tools",
            "permission_mode": "accept_edits",
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read file contents",
                    "read_only": True,
                    "approval_required": False,
                },
                {
                    "name": "web_fetch",
                    "description": "Fetch readable content from one public HTTPS URL",
                    "read_only": True,
                    "approval_required": True,
                },
                {
                    "name": "web_search",
                    "description": "Search the public web for current information",
                    "read_only": True,
                    "approval_required": True,
                },
            ],
        },
        _model(
            WebStatusCommandPayload(
                enabled=True,
                available=True,
                credential_configured=True,
                proxy_configured=False,
                thread_authorized=False,
                requests_per_turn=8,
                disclosure=TAVILY_DISCLOSURE,
            )
        ),
        {
            "kind": "skills",
            "active_mode": "auto",
            "skills": [
                {
                    "name": "coding",
                    "description": "Coding workflow",
                    "source": "bundled",
                }
            ],
            "diagnostics": [],
        },
        {
            "kind": "mcp",
            "servers": [{"server_id": "docs", "state": "configured", "detail": None}],
        },
        {
            "kind": "memory_status",
            "local_available": True,
            "local_enabled": False,
            "cloud_provider": "mem0",
            "cloud_available": False,
            "cloud_enabled": False,
            "cloud_error_code": None,
        },
        {
            "kind": "memory_document",
            "scope": "user",
            "content_hash": "a" * 64,
            "entries": [{"id": "entry_1", "content": "Fact"}],
        },
        {
            "kind": "memory_search",
            "provider": "mem0",
            "memories": [
                {
                    "id": "memory_1",
                    "content": "Fact",
                    "scope": "user",
                    "fact_hash": "b" * 64,
                }
            ],
        },
        {
            "kind": "memory_mutation",
            "provider": "local",
            "status": "stored",
            "scope": "user",
            "entry_id": "entry_1",
            "memory_id": None,
            "error_code": None,
        },
        {"kind": "status", "snapshot": status},
        {"kind": "usage", "usage": usage},
        {
            "kind": "doctor",
            "checks": [{"name": "SQLite", "status": "ok", "detail": None}],
        },
        {"kind": "config", "sources": ["user"], "credentials": credentials},
        {"kind": "permissions", "mode": "accept_edits"},
    ]
    cases: list[dict[str, object]] = []
    for payload in payloads:
        outcome = COMMAND_OUTCOME_ADAPTER.validate_python(
            {"kind": "result", "payload": payload}
        )
        cases.append({"name": f"result.{payload['kind']}", "outcome": _model(outcome)})
    cases.append(
        {
            "name": "result.thread_transition.fork",
            "outcome": _model(
                COMMAND_OUTCOME_ADAPTER.validate_python(
                    {"kind": "result", "payload": fork_payload}
                )
            ),
        }
    )
    for name, raw in (
        (
            "interaction.selection",
            {
                "kind": "interaction",
                "interaction": {
                    "kind": "selection",
                    "prompt": "Choose",
                    "options": [
                        {
                            "value": "one",
                            "label": "One",
                            "selected": False,
                            "disabled": False,
                        }
                    ],
                },
            },
        ),
        (
            "interaction.secret",
            {
                "kind": "interaction",
                "interaction": {
                    "kind": "secret",
                    "provider": "deepseek",
                    "action": "add",
                    "label": "DeepSeek API Key",
                    "environment_variable": "DEEPSEEK_API_KEY",
                    "help_url": "https://platform.deepseek.com/api_keys",
                },
            },
        ),
        (
            "interaction.application",
            {
                "kind": "interaction",
                "interaction": {
                    "kind": "application",
                    "interaction_id": "interaction_1",
                },
            },
        ),
        (
            "error.invalid_arguments",
            {
                "kind": "error",
                "code": "invalid_arguments",
                "message": "Invalid arguments.",
            },
        ),
    ):
        cases.append(
            {
                "name": name,
                "outcome": _model(COMMAND_OUTCOME_ADAPTER.validate_python(raw)),
            }
        )
    return {"cases": cases}


def _invalid_command_results() -> dict[str, object]:
    methods = cast(list[dict[str, Any]], _valid_methods()["cases"])
    application = next(
        case["result"]["value"]
        for case in methods
        if case["name"] == "application.get_state"
    )
    thread = next(
        case["result"]["value"] for case in methods if case["name"] == "thread.read"
    )
    retry_outcome = next(
        case["outcome"]
        for case in cast(list[dict[str, Any]], _valid_command_results()["cases"])
        if case["name"] == "result.thread_retry"
    )
    retry_payload = cast(dict[str, Any], retry_outcome["payload"])
    retry_transition = cast(dict[str, Any], retry_payload["transition"])
    retry_thread = cast(dict[str, Any], retry_transition["thread"])
    retry_view = cast(dict[str, Any], retry_thread["view"])
    retry_thread_record = cast(dict[str, Any], retry_view["thread"])
    retry_entries = cast(list[dict[str, Any]], retry_view["entries"])
    retry_turns = cast(list[dict[str, Any]], retry_view["turns"])
    retry_turn = retry_turns[-1]
    retry_operation = cast(dict[str, Any], retry_payload["operation"])
    fork_outcome = next(
        case["outcome"]
        for case in cast(list[dict[str, Any]], _valid_command_results()["cases"])
        if case["name"] == "result.thread_transition.fork"
    )
    fork_payload = cast(dict[str, Any], fork_outcome["payload"])
    fork_transition = cast(dict[str, Any], fork_payload["transition"])
    fork_thread = cast(dict[str, Any], fork_transition["thread"])
    fork_view = cast(dict[str, Any], fork_thread["view"])
    fork_thread_record = cast(dict[str, Any], fork_view["thread"])
    web_status = {
        "kind": "web_status",
        "enabled": False,
        "provider": "tavily",
        "available": False,
        "credential_configured": False,
        "proxy_configured": False,
        "thread_authorized": False,
        "requests_per_turn": 8,
        "disclosure": TAVILY_DISCLOSURE,
    }
    return {
        "cases": [
            {
                "name": "legacy",
                "outcome": {"status": "success", "content": "", "data": {}},
            },
            {
                "name": "unknown_payload",
                "outcome": {"kind": "result", "payload": {"kind": "unknown"}},
            },
            {
                "name": "thread_transition_identity_mismatch",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        "kind": "thread_transition",
                        "transition": {
                            "reason": "resume",
                            "application": {
                                **application,
                                "current_thread_id": (
                                    "thread_22222222222222222222222222222222"
                                ),
                            },
                            "thread": thread,
                        },
                    },
                },
            },
            {
                "name": "thread_transition_retry_requires_combined_payload",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        "kind": "thread_transition",
                        "transition": retry_transition,
                    },
                },
            },
            {
                "name": "thread_transition_fork_requires_lineage",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        **fork_payload,
                        "transition": {
                            **fork_transition,
                            "thread": {
                                **fork_thread,
                                "view": {
                                    **fork_view,
                                    "thread": {
                                        **fork_thread_record,
                                        "lineage": None,
                                    },
                                },
                            },
                        },
                    },
                },
            },
            {
                "name": "thread_transition_new_requires_null_lineage",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        **fork_payload,
                        "transition": {**fork_transition, "reason": "new"},
                    },
                },
            },
            {
                "name": "thread_retry_wrong_transition_reason",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        **retry_payload,
                        "transition": {**retry_transition, "reason": "fork"},
                    },
                },
            },
            {
                "name": "thread_retry_wrong_lineage_kind",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        **retry_payload,
                        "transition": {
                            **retry_transition,
                            "thread": {
                                **retry_thread,
                                "view": {
                                    **retry_view,
                                    "thread": {
                                        **retry_thread_record,
                                        "lineage": {
                                            **retry_thread_record["lineage"],
                                            "kind": "fork",
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            {
                "name": "thread_retry_operation_thread_mismatch",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        **retry_payload,
                        "operation": {
                            **retry_operation,
                            "thread_id": THREAD_ID,
                        },
                    },
                },
            },
            {
                "name": "thread_retry_operation_turn_missing",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        **retry_payload,
                        "operation": {
                            **retry_operation,
                            "turn_id": TURN_ID,
                        },
                    },
                },
            },
            {
                "name": "thread_retry_operation_client_message_mismatch",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        **retry_payload,
                        "operation": {
                            **retry_operation,
                            "client_message_id": (
                                "client_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                            ),
                        },
                    },
                },
            },
            {
                "name": "thread_retry_operation_user_entry_missing",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        **retry_payload,
                        "transition": {
                            **retry_transition,
                            "thread": {
                                **retry_thread,
                                "view": {
                                    **retry_view,
                                    "turns": [
                                        {
                                            **retry_turn,
                                            "user_entry_id": (
                                                "entry_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                                            ),
                                        }
                                    ],
                                },
                            },
                        },
                    },
                },
            },
            {
                "name": "thread_retry_operation_turn_terminal",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        **retry_payload,
                        "transition": {
                            **retry_transition,
                            "thread": {
                                **retry_thread,
                                "view": {
                                    **retry_view,
                                    "turns": [
                                        {
                                            **retry_turn,
                                            "status": "cancelled",
                                            "termination_reason": "cancelled",
                                            "completed_at": retry_turn["updated_at"],
                                        }
                                    ],
                                },
                            },
                        },
                    },
                },
            },
            {
                "name": "thread_retry_operation_turn_not_last",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        **retry_payload,
                        "transition": {
                            **retry_transition,
                            "thread": {
                                **retry_thread,
                                "view": {
                                    **retry_view,
                                    "turns": [
                                        *retry_turns,
                                        {
                                            **retry_turn,
                                            "id": (
                                                "turn_cccccccccccccccccccccccccccccccc"
                                            ),
                                            "checkpoint_key": (
                                                "turn_cccccccccccccccccccccccccccccccc"
                                            ),
                                            "status": "cancelled",
                                            "termination_reason": "cancelled",
                                            "completed_at": retry_turn["updated_at"],
                                        },
                                    ],
                                },
                            },
                        },
                    },
                },
            },
            {
                "name": "thread_retry_operation_multiple_in_progress",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        **retry_payload,
                        "transition": {
                            **retry_transition,
                            "thread": {
                                **retry_thread,
                                "view": {
                                    **retry_view,
                                    "entries": retry_entries,
                                    "turns": [
                                        {
                                            **retry_turn,
                                            "id": (
                                                "turn_dddddddddddddddddddddddddddddddd"
                                            ),
                                            "checkpoint_key": (
                                                "turn_dddddddddddddddddddddddddddddddd"
                                            ),
                                        },
                                        *retry_turns,
                                    ],
                                },
                            },
                        },
                    },
                },
            },
            {
                "name": "thread_retry_unknown_field",
                "outcome": {
                    "kind": "result",
                    "payload": {**retry_payload, "buffered_events": []},
                },
            },
            {
                "name": "workspace_instruction_unknown_code",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        "kind": "thread_transition",
                        "transition": {
                            "reason": "resume",
                            "application": {
                                **application,
                                "workspace_instruction_diagnostic": {
                                    **application["workspace_instruction_diagnostic"],
                                    "code": "workspace_instructions_future",
                                },
                            },
                            "thread": thread,
                        },
                    },
                },
            },
            {
                "name": "workspace_instruction_unknown_source",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        "kind": "thread_transition",
                        "transition": {
                            "reason": "resume",
                            "application": {
                                **application,
                                "workspace_instruction_diagnostic": {
                                    **application["workspace_instruction_diagnostic"],
                                    "source_id": "PROJECT.md",
                                },
                            },
                            "thread": thread,
                        },
                    },
                },
            },
            {
                "name": "web_status_empty_diagnostic_code",
                "outcome": {
                    "kind": "result",
                    "payload": {**web_status, "diagnostic_code": ""},
                },
            },
            {
                "name": "thread_export_changed_without_change_set",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        "kind": "thread_export",
                        "thread_id": THREAD_ID,
                        "path": "exports/fixture.md",
                        "format": "markdown",
                        "write_status": "updated",
                        "byte_count": 1_024,
                    },
                },
            },
            {
                "name": "thread_export_unchanged_with_change_set",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        "kind": "thread_export",
                        "thread_id": THREAD_ID,
                        "path": "exports/fixture.json",
                        "format": "json",
                        "write_status": "unchanged",
                        "byte_count": 1_024,
                        "change_set_id": "change_1",
                    },
                },
            },
            {
                "name": "thread_export_empty_change_set",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        "kind": "thread_export",
                        "thread_id": THREAD_ID,
                        "path": "exports/fixture.md",
                        "format": "markdown",
                        "write_status": "created",
                        "byte_count": 1_024,
                        "change_set_id": "",
                    },
                },
            },
            {
                "name": "thread_export_path_too_long",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        "kind": "thread_export",
                        "thread_id": THREAD_ID,
                        "path": "x" * 1_001,
                        "format": "markdown",
                        "write_status": "created",
                        "byte_count": 1_024,
                        "change_set_id": "change_1",
                    },
                },
            },
            {
                "name": "web_status_invalid_diagnostic_code",
                "outcome": {
                    "kind": "result",
                    "payload": {
                        **web_status,
                        "diagnostic_code": "Web-Client-Unavailable",
                    },
                },
            },
            {
                "name": "secret_value",
                "outcome": {
                    "kind": "interaction",
                    "interaction": {
                        "kind": "secret",
                        "provider": "deepseek",
                        "action": "add",
                        "label": "Key",
                        "environment_variable": "DEEPSEEK_API_KEY",
                        "help_url": "https://example.com",
                        "api_key": "private",
                    },
                },
            },
            {
                "name": "duplicate_options",
                "outcome": {
                    "kind": "interaction",
                    "interaction": {
                        "kind": "selection",
                        "prompt": "Choose",
                        "options": [
                            {"value": "same", "label": "One"},
                            {"value": "same", "label": "Two"},
                        ],
                    },
                },
            },
        ]
    }


def build_files() -> dict[str, bytes]:
    files = {
        "command-results.invalid.json": _json_bytes(_invalid_command_results()),
        "command-results.valid.json": _json_bytes(_valid_command_results()),
        "commands.json": _json_bytes(_commands()),
        "events.invalid.json": _json_bytes(_invalid_events()),
        "events.valid.json": _json_bytes(_valid_events()),
        "methods.invalid.json": _json_bytes(_invalid_methods(), ensure_ascii=True),
        "methods.valid.json": _json_bytes(_valid_methods()),
        "results.failures.json": _json_bytes(_failure_results()),
    }
    manifest = {
        "fixture_version": 1,
        "product_version": PRODUCT_VERSION,
        "protocol_version": PROTOCOL_VERSION,
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
