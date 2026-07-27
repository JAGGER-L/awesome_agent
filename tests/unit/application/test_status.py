from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from awesome_agent.application.command_results import (
    ContextCommandPayload,
    StatusCommandPayload,
    UsageCommandPayload,
)
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.composition import compose_local_application
from awesome_agent.application.contracts import StatusSnapshot, thread_display_id
from awesome_agent.config import BudgetConfig, TurnConfig
from awesome_agent.conversation import (
    ConversationService,
    UsageSummary,
)
from awesome_agent.core.changes import (
    ChangeLifecycle,
    ChangeReversibility,
    ChangeSet,
    FileChange,
    FileChangeKind,
    FileNodeType,
)
from awesome_agent.core.events import CollectingEventSink
from awesome_agent.core.tools import ToolActivityDraft, ToolExecutionOrigin
from awesome_agent.core.workspace import WorkspaceTrustService, resolve_workspace
from awesome_agent.modeling import ModelIdentitySnapshot
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage import ApplicationSQLite
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore
from awesome_agent.storage.conversations import SQLiteConversationRepositories
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore
from awesome_agent.version import PRODUCT_VERSION


def test_status_snapshot_is_exact_and_resume_friendly() -> None:
    full_thread_id = "thread_3f8a1c2d111122223333444455556666"
    snapshot = StatusSnapshot(
        version=PRODUCT_VERSION,
        workspace_path="C:\\workspace",
        thread_title="Feature auth",
        thread_id=full_thread_id,
        thread_display_id=thread_display_id(full_thread_id),
        model_identity=ModelIdentitySnapshot.from_models(
            configured_model="deepseek/deepseek-v4-flash",
            effective_model="deepseek/deepseek-v4-flash",
        ),
        model_status="configured",
        thinking_enabled=False,
        skill_mode="auto",
        local_memory_enabled=False,
        mem0_enabled=False,
        mcp_ready=2,
        mcp_degraded=0,
        operation_status="idle",
        operation_id=None,
        configuration_valid=True,
        configuration_diagnostic_count=0,
    )

    assert snapshot.thread_display_id == "thread_3f8a1c2d"
    assert snapshot.model_dump(mode="json")["model_identity"]["fallback_from"] is None
    assert set(snapshot.model_dump(mode="json")) == {
        "version",
        "workspace_path",
        "thread_title",
        "thread_id",
        "thread_display_id",
        "model_identity",
        "model_status",
        "thinking_enabled",
        "skill_mode",
        "local_memory_enabled",
        "mem0_enabled",
        "mcp_ready",
        "mcp_degraded",
        "operation_status",
        "operation_id",
        "configuration_valid",
        "configuration_diagnostic_count",
        "permission_mode",
        "credential_source",
        "credential_source_available",
        "context_used_tokens",
        "context_budget_tokens",
        "changed_file_count",
    }
    serialized = snapshot.model_dump_json()
    for excluded in ("trusted", "branch", "usage", "secret", "database", "dirty"):
        assert excluded not in serialized.casefold()

    colliding = "thread_3f8a1c2d999922223333444455556666"
    assert (
        thread_display_id(
            full_thread_id,
            candidate_ids=(full_thread_id, colliding),
        )
        == "thread_3f8a1c2d1"
    )


@pytest.mark.asyncio
async def test_status_command_returns_typed_snapshot_not_application_dump(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AwesomePaths.from_home(home)
    identity = resolve_workspace(workspace)
    database = ApplicationSQLite(paths.application_db)
    await database.initialize()
    await WorkspaceTrustService(SQLiteWorkspaceTrustStore(database)).accept(identity)
    repositories = SQLiteConversationRepositories(database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(identity.key, "Feature auth")
    config = TurnConfig(
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        budgets=BudgetConfig(),
    )
    completed = await conversation.begin_turn(
        thread.id, "completed", config, client_message_id="client_completed"
    )
    await conversation.complete_turn(
        completed.id,
        "done",
        UsageSummary(input_tokens=10, output_tokens=4, model_calls=1),
        "completed",
        (
            {
                "kind": "recent_turns",
                "source_id": "recent",
                "order": 0,
                "estimated_tokens": 100,
                "truncated": False,
                "content_hash": "a" * 64,
            },
        ),
    )
    failed = await conversation.begin_turn(
        thread.id, "failed", config, client_message_id="client_failed"
    )
    await conversation.fail_turn(
        failed.id,
        "model_failed",
        usage=UsageSummary(input_tokens=5, tool_calls=1),
        context_manifest=(
            {
                "kind": "thread_summary",
                "source_id": "summary",
                "order": 0,
                "estimated_tokens": 50,
                "truncated": False,
                "content_hash": "b" * 64,
            },
        ),
    )
    cancelled = await conversation.begin_turn(
        thread.id, "cancelled", config, client_message_id="client_cancelled"
    )
    await conversation.cancel_turn(
        cancelled.id,
        usage=UsageSummary(input_tokens=2, active_execution_seconds=0.5),
    )
    observed_at = datetime.now(UTC)
    blobs = FileChangeBlobStore(paths.change_journal_dir)
    first_blob = blobs.put(b"first")
    updated_blob = blobs.put(b"updated")
    second_blob = blobs.put(b"second")
    change_set = ChangeSet(
        id="change_agent",
        session_id="session_previous",
        turn_id=completed.id,
        workspace_key=identity.key,
        lifecycle=ChangeLifecycle.APPLIED,
        reversibility=ChangeReversibility.FULL,
        files=[
            FileChange(
                mutation_id="mutation_1",
                path="src/first.py",
                kind=FileChangeKind.CREATED,
                node_type=FileNodeType.FILE,
                after_node_type=FileNodeType.FILE,
                after_hash=first_blob,
                after_blob=first_blob,
            ),
            FileChange(
                mutation_id="mutation_2",
                path="src/first.py",
                kind=FileChangeKind.UPDATED,
                node_type=FileNodeType.FILE,
                after_node_type=FileNodeType.FILE,
                after_hash=updated_blob,
                after_blob=updated_blob,
            ),
            FileChange(
                mutation_id="mutation_3",
                path="src/second.py",
                kind=FileChangeKind.CREATED,
                node_type=FileNodeType.FILE,
                after_node_type=FileNodeType.FILE,
                after_hash=second_blob,
                after_blob=second_blob,
            ),
        ],
        created_at=observed_at,
        sealed_at=observed_at,
    )
    change_store = SQLiteChangeSetStore(database)
    await change_store.save(change_set)
    await repositories.finalize(
        ToolActivityDraft(
            thread_id=thread.id,
            turn_id=completed.id,
            operation_id="operation_agent",
            call_id="call_agent",
            origin=ToolExecutionOrigin.AGENT,
            tool_name="write_file",
            outcome="success",
            change_set_id=change_set.id,
            duration_ms=1,
        )
    )
    await repositories.finalize(
        ToolActivityDraft(
            thread_id=thread.id,
            turn_id=None,
            operation_id="operation_direct",
            call_id="call_direct",
            origin=ToolExecutionOrigin.DIRECT,
            tool_name="execute",
            outcome="success",
            change_set_id=change_set.id,
            duration_ms=1,
        )
    )
    await database.aclose()
    application = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={"DEEPSEEK_API_KEY": "fake-key"},
    )

    assert (await application.initialize()).ok is True
    resumed = await application.execute_command(
        CommandIntent(name=CommandName.RESUME, arguments=(thread.id,))
    )
    assert resumed.ok is True
    status = await application.execute_command(CommandIntent(name=CommandName.STATUS))
    usage = await application.execute_command(CommandIntent(name=CommandName.USAGE))
    context = await application.execute_command(CommandIntent(name=CommandName.CONTEXT))
    assert status.ok is True
    assert status.value is not None
    assert status.value.kind == "result"
    assert isinstance(status.value.payload, StatusCommandPayload)
    snapshot = status.value.payload.snapshot
    application_state = await application.get_state()
    assert application_state.ok is True
    assert application_state.value is not None
    assert snapshot.version == PRODUCT_VERSION
    assert snapshot.workspace_path == str(workspace)
    assert snapshot.thread_title == "Feature auth"
    assert snapshot.thread_id == thread.id
    assert snapshot.thread_display_id == thread_display_id(thread.id)
    assert snapshot.model_identity == application_state.value.model_identity
    assert snapshot.model_identity.provider == "deepseek"
    assert snapshot.model_identity.effective_model == ("deepseek/deepseek-v4-flash")
    assert snapshot.model_identity.fallback_active is False
    assert snapshot.model_status == "configured"
    assert snapshot.operation_status == "idle"
    assert snapshot.local_memory_enabled is False
    assert snapshot.mem0_enabled is False
    assert snapshot.configuration_valid is True
    assert snapshot.configuration_diagnostic_count == 0
    assert snapshot.context_used_tokens == 50
    assert snapshot.changed_file_count == 2
    assert usage.ok is True
    assert usage.value is not None
    assert usage.value.kind == "result"
    assert isinstance(usage.value.payload, UsageCommandPayload)
    assert usage.value.payload.usage == UsageSummary(
        input_tokens=17,
        output_tokens=4,
        model_calls=1,
        tool_calls=1,
        active_execution_seconds=0.5,
    )
    assert context.ok is True
    assert context.value is not None
    assert context.value.kind == "result"
    assert isinstance(context.value.payload, ContextCommandPayload)
    assert context.value.payload.total_tokens == snapshot.context_used_tokens
    assert "secret_status" not in status.value.model_dump(mode="json")

    external_database = ApplicationSQLite(paths.application_db)
    await external_database.initialize()
    try:
        await SQLiteChangeSetStore(external_database).save(
            change_set.model_copy(update={"lifecycle": ChangeLifecycle.UNDONE})
        )
    finally:
        await external_database.aclose()
    undone_status = await application.execute_command(
        CommandIntent(name=CommandName.STATUS)
    )
    assert undone_status.ok is True
    assert undone_status.value is not None
    assert undone_status.value.kind == "result"
    assert isinstance(undone_status.value.payload, StatusCommandPayload)
    assert undone_status.value.payload.snapshot.changed_file_count == 0
    await application.shutdown()
