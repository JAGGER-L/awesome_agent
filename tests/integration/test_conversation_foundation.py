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
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage.application_sqlite import ApplicationSQLite
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore
from awesome_agent.storage.conversations import SQLiteConversationRepositories
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore


async def test_fresh_home_multi_thread_history_survives_restart_without_checkpoint(
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
    database = ApplicationSQLite(paths.application_db)
    await database.initialize()
    try:
        service = ConversationService(store=SQLiteConversationRepositories(database))
        first = await service.create_thread("workspace_key", "First")
        second = await service.create_thread("workspace_key", "Second")
        turn = await service.begin_turn(
            first.id, "Inspect", turn_config, client_message_id="client_inspect"
        )
        await service.complete_turn(
            turn.id,
            "Inspection complete",
            UsageSummary(input_tokens=20, output_tokens=5, model_calls=1),
            "completed",
        )
        await service.append_direct_command(
            first.id,
            "$ pytest\nexit=0",
            {"exit_code": 0},
        )
        await service.begin_turn(
            second.id,
            "Independent",
            turn_config,
            client_message_id="client_independent",
        )
    finally:
        await database.aclose()

    reopened_database = ApplicationSQLite(paths.application_db)
    await reopened_database.initialize()
    try:
        reopened = ConversationService(
            store=SQLiteConversationRepositories(reopened_database)
        )
        first_view = await reopened.read_thread(first.id)
        second_view = await reopened.read_thread(second.id)
    finally:
        await reopened_database.aclose()

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
    database = ApplicationSQLite(paths.application_db)
    await database.initialize()
    repositories = SQLiteConversationRepositories(database)
    try:
        await WorkspaceTrustService(SQLiteWorkspaceTrustStore(database)).accept(
            identity
        )
        conversation = ConversationService(store=repositories)
        thread = await conversation.create_thread(identity.key, "Changes")
        await conversation.append_direct_command(
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
        await SQLiteChangeSetStore(database).save(change_set)
        await repositories.finalize(
            ToolActivityDraft(
                thread_id=thread.id,
                turn_id=None,
                operation_id="operation_1",
                call_id="call_1",
                origin=ToolExecutionOrigin.DIRECT,
                tool_name="execute",
                outcome="success",
                change_set_id=change_set.id,
                duration_ms=1,
            )
        )
    finally:
        await database.aclose()
    application = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    try:
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
    finally:
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
    database = ApplicationSQLite(paths.application_db)
    await database.initialize()
    try:
        await WorkspaceTrustService(SQLiteWorkspaceTrustStore(database)).accept(
            identity
        )
        repositories = SQLiteConversationRepositories(database)
        conversation = ConversationService(store=repositories)
        thread = await conversation.create_thread(identity.key, "Large page")
        for index in range(100):
            await conversation.append_direct_command(
                thread.id,
                "x" * 20_000,
                {"operation_id": f"operation_{index}"},
            )
    finally:
        await database.aclose()
    application = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    try:
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
    finally:
        await application.shutdown()
