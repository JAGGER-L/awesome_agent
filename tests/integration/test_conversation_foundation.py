from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from awesome_agent.application.composition import compose_local_application
from awesome_agent.application.contracts import ThreadReadQuery
from awesome_agent.config import (
    ThreadConfigState,
    load_config_sources,
    resolve_application_config,
    resolve_turn_config,
)
from awesome_agent.conversation import (
    ConversationService,
    ThreadEntryKind,
    ToolActivity,
    ToolActivityOrigin,
    ToolActivityOutcome,
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
from awesome_agent.core.workspace import WorkspaceTrustService, resolve_workspace
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore
from awesome_agent.storage.conversations import SQLiteConversationRepositories
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore


def test_fresh_home_multi_thread_history_survives_restart_without_checkpoint(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AwesomePaths.from_home(home)
    loaded = load_config_sources(
        paths=paths,
        workspace=workspace,
        workspace_trusted=True,
        environ={"DEEPSEEK_API_KEY": "test-secret"},
    )
    application = resolve_application_config(loaded)
    turn_config = resolve_turn_config(
        application,
        thread=ThreadConfigState(),
        environ={},
    )
    service = ConversationService(
        store=SQLiteConversationRepositories(paths.application_db)
    )
    first = service.create_thread("workspace_key", "First")
    second = service.create_thread("workspace_key", "Second")
    turn = service.begin_turn(
        first.id, "Inspect", turn_config, client_message_id="client_inspect"
    )
    service.complete_turn(
        turn.id,
        "Inspection complete",
        UsageSummary(input_tokens=20, output_tokens=5, model_calls=1),
        "completed",
    )
    service.append_direct_command(
        first.id,
        "$ pytest\nexit=0",
        {"exit_code": 0},
    )
    service.begin_turn(
        second.id,
        "Independent",
        turn_config,
        client_message_id="client_independent",
    )

    reopened = ConversationService(
        store=SQLiteConversationRepositories(paths.application_db)
    )
    first_view = reopened.read_thread(first.id)
    second_view = reopened.read_thread(second.id)

    assert [entry.kind for entry in first_view.entries] == [
        ThreadEntryKind.USER_MESSAGE,
        ThreadEntryKind.ASSISTANT_MESSAGE,
        ThreadEntryKind.DIRECT_COMMAND,
    ]
    assert first_view.turns[0].status.value == "completed"
    assert second_view.turns[0].status.value == "in_progress"
    assert paths.application_db.is_file()
    assert not paths.checkpoint_db.exists()
    assert "test-secret" not in paths.application_db.read_bytes().decode(
        "utf-8",
        errors="ignore",
    )


@pytest.mark.asyncio
async def test_surface_thread_page_projects_safe_change_set_summary(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AwesomePaths.from_home(home)
    identity = resolve_workspace(workspace)
    WorkspaceTrustService(SQLiteWorkspaceTrustStore(paths.application_db)).accept(
        identity
    )
    repositories = SQLiteConversationRepositories(paths.application_db)
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(identity.key, "Changes")
    conversation.append_direct_command(
        thread.id,
        "[direct command]\nstatus: success",
        {"operation_id": "operation_1"},
    )
    now = datetime.now(UTC)
    blobs = FileChangeBlobStore(paths.change_journal_dir)
    before_blob = blobs.put(b"before\n")
    after_blob = blobs.put(b"after\n")
    change_set = ChangeSet(
        id="change_1",
        session_id="session_1",
        turn_id=None,
        workspace_key=identity.key,
        lifecycle=ChangeLifecycle.APPLIED,
        reversibility=ChangeReversibility.FULL,
        files=[
            FileChange(
                path="src/example.py",
                kind=FileChangeKind.UPDATED,
                node_type=FileNodeType.FILE,
                before_hash=before_blob,
                after_hash=after_blob,
                before_blob=before_blob,
                after_blob=after_blob,
            )
        ],
        created_at=now,
        sealed_at=now,
    )
    SQLiteChangeSetStore(paths.application_db).save(change_set)
    repositories.tool_activities.append(
        ToolActivity(
            id="activity_1",
            thread_id=thread.id,
            turn_id=None,
            operation_id="operation_1",
            call_id="call_1",
            sequence=1,
            origin=ToolActivityOrigin.DIRECT,
            tool_name="execute",
            outcome=ToolActivityOutcome.SUCCESS,
            change_set_id=change_set.id,
            duration_ms=1,
            created_at=now,
        )
    )
    application = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    initialized = await application.initialize()
    assert initialized.ok is True
    result = await application.read_thread(ThreadReadQuery(thread_id=thread.id))
    assert result.ok is True
    assert result.value is not None
    assert len(result.value.change_sets) == 1
    summary = result.value.change_sets[0]
    assert summary.change_set_id == change_set.id
    assert summary.operation_id == "operation_1"
    assert tuple(change.path for change in summary.changes) == ("src/example.py",)
    assert before_blob not in summary.model_dump_json()
    assert after_blob not in summary.model_dump_json()
    await application.shutdown()


@pytest.mark.asyncio
async def test_surface_thread_page_stays_below_protocol_frame_budget(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AwesomePaths.from_home(home)
    identity = resolve_workspace(workspace)
    WorkspaceTrustService(SQLiteWorkspaceTrustStore(paths.application_db)).accept(
        identity
    )
    repositories = SQLiteConversationRepositories(paths.application_db)
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(identity.key, "Large page")
    for index in range(100):
        conversation.append_direct_command(
            thread.id,
            "x" * 20_000,
            {"operation_id": f"operation_{index}"},
        )
    application = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    assert (await application.initialize()).ok is True
    result = await application.read_thread(
        ThreadReadQuery(thread_id=thread.id, limit=100)
    )

    assert result.ok is True
    assert result.value is not None
    assert result.value.has_more is True
    assert result.value.next_before_sequence is not None
    assert len(result.value.view.entries) < 100
    assert len(result.model_dump_json().encode("utf-8")) <= 900_000
    await application.shutdown()
