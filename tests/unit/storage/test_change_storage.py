import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from awesome_agent.core.changes import (
    ChangeLifecycle,
    ChangeReversibility,
    ChangeSet,
    ExecuteObservation,
    FileChange,
    FileChangeKind,
    FileNodeType,
)
from awesome_agent.core.changes.errors import ChangeBlobCorrupt
from awesome_agent.core.changes.ports import PendingMutation
from awesome_agent.storage.application_sqlite import ApplicationSQLite
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore
from awesome_agent.storage.database import (
    APPLICATION_SCHEMA_VERSION,
    ApplicationSchemaMismatch,
)


@pytest.fixture
async def application_database(tmp_path: Path) -> AsyncIterator[ApplicationSQLite]:
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.aclose()


async def test_current_schema_contains_change_and_pending_tables(
    application_database: ApplicationSQLite,
) -> None:
    def inspect(connection: sqlite3.Connection) -> tuple[set[str], int]:
        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        return names, version

    names, version = await application_database.read(inspect)
    assert APPLICATION_SCHEMA_VERSION == version == 8
    assert {"change_sets", "pending_mutations"} <= names


async def test_schema_one_is_rejected_without_a_compatibility_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "application.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE trusted_workspaces ("
            "workspace_key TEXT PRIMARY KEY, canonical_path TEXT NOT NULL, "
            "trusted_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO trusted_workspaces VALUES (?, ?, ?)",
            ("ws_1", "C:/workspace", datetime.now(UTC).isoformat()),
        )
        connection.execute("PRAGMA user_version = 1")

    database = ApplicationSQLite(path)
    try:
        with pytest.raises(ApplicationSchemaMismatch) as raised:
            await database.initialize()
    finally:
        await database.aclose()

    assert raised.value.found == 1
    assert raised.value.expected == APPLICATION_SCHEMA_VERSION


def test_blob_store_is_content_addressed_and_detects_corruption(
    tmp_path: Path,
) -> None:
    store = FileChangeBlobStore(tmp_path / "change-journal")
    first = store.put(b"content")
    second = store.put(b"content")
    assert first == second
    assert store.get(first) == b"content"

    blob = tmp_path / "change-journal" / "blobs" / first[:2] / first
    blob.write_bytes(b"corrupt")
    with pytest.raises(ChangeBlobCorrupt):
        store.get(first)


async def test_change_set_and_pending_mutation_survive_reopen(
    application_database: ApplicationSQLite,
) -> None:
    created_at = datetime.now(UTC)
    change_set = ChangeSet(
        id="change_1",
        session_id="session_1",
        turn_id="turn_1",
        workspace_key="ws_1",
        lifecycle=ChangeLifecycle.OPEN,
        reversibility=ChangeReversibility.FULL,
        created_at=created_at,
    )
    pending = PendingMutation(
        id="pending_1",
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path="src/app.py",
        kind=FileChangeKind.CREATED,
        node_type=FileNodeType.FILE,
        before_hash=None,
        before_blob=None,
        before_mode=None,
        intended_after_hash="a" * 64,
        intended_after_blob="a" * 64,
        intended_after_mode=0o644,
        created_at=created_at,
    )

    first = SQLiteChangeSetStore(application_database)
    await first.save(change_set)
    await first.save_pending(pending)

    reopened = SQLiteChangeSetStore(application_database)
    assert await reopened.get(change_set.id) == change_set
    assert await reopened.latest("ws_1") == change_set
    assert await reopened.list_pending() == [pending]
    await reopened.delete_pending(pending.id)
    assert await reopened.list_pending() == []


async def test_pending_distinct_node_types_survive_reopen_without_a_schema_change(
    application_database: ApplicationSQLite,
) -> None:
    created_at = datetime.now(UTC)
    change_set = ChangeSet(
        id="change_type_transition",
        session_id="session_1",
        turn_id="turn_1",
        workspace_key="ws_1",
        lifecycle=ChangeLifecycle.APPLIED,
        reversibility=ChangeReversibility.FULL,
        created_at=created_at,
        sealed_at=created_at,
    )
    pending = PendingMutation(
        id="undo_type_transition_0000",
        change_set_id=change_set.id,
        workspace_key=change_set.workspace_key,
        relative_path="node",
        kind=FileChangeKind.UPDATED,
        node_type=FileNodeType.DIRECTORY,
        before_node_type=FileNodeType.FILE,
        intended_after_node_type=FileNodeType.DIRECTORY,
        before_hash="a" * 64,
        before_blob="a" * 64,
        before_mode=0o644,
        intended_after_hash="b" * 64,
        intended_after_blob=None,
        intended_after_mode=0o755,
        created_at=created_at,
    )
    store = SQLiteChangeSetStore(application_database)
    await store.save(change_set)
    await store.save_pending(pending)

    reopened = SQLiteChangeSetStore(application_database)

    assert APPLICATION_SCHEMA_VERSION == 8
    reopened_pending = await reopened.list_pending()
    assert reopened_pending == [pending]
    assert reopened_pending[0].before_node_type is FileNodeType.FILE
    assert reopened_pending[0].intended_after_node_type is FileNodeType.DIRECTORY


async def test_list_open_is_scoped_to_the_workspace(
    application_database: ApplicationSQLite,
) -> None:
    store = SQLiteChangeSetStore(application_database)
    created_at = datetime.now(UTC)
    open_change = ChangeSet(
        id="change_open",
        session_id="session_1",
        turn_id="turn_1",
        workspace_key="ws_1",
        lifecycle=ChangeLifecycle.OPEN,
        reversibility=ChangeReversibility.FULL,
        created_at=created_at,
    )
    applied_change = open_change.model_copy(
        update={
            "id": "change_applied",
            "lifecycle": ChangeLifecycle.APPLIED,
            "sealed_at": created_at,
        }
    )
    foreign_change = open_change.model_copy(
        update={"id": "change_foreign", "workspace_key": "ws_2"}
    )
    for change_set in (open_change, applied_change, foreign_change):
        await store.save(change_set)

    assert await store.list_open("ws_1") == [open_change]


async def test_delete_empty_open_rechecks_evidence_and_pending_atomically(
    application_database: ApplicationSQLite,
) -> None:
    store = SQLiteChangeSetStore(application_database)
    created_at = datetime.now(UTC)
    empty = ChangeSet(
        id="change_empty",
        session_id="session_1",
        turn_id=None,
        workspace_key="ws_1",
        lifecycle=ChangeLifecycle.OPEN,
        reversibility=ChangeReversibility.FULL,
        created_at=created_at,
    )
    applied = empty.model_copy(
        update={
            "id": "change_applied",
            "lifecycle": ChangeLifecycle.APPLIED,
            "sealed_at": created_at,
        }
    )
    with_file = empty.model_copy(
        update={
            "id": "change_file",
            "files": [
                FileChange(
                    path="out.md",
                    kind=FileChangeKind.CREATED,
                    node_type=FileNodeType.FILE,
                    after_hash="a" * 64,
                    after_blob="a" * 64,
                )
            ],
        }
    )
    with_execute = empty.model_copy(
        update={
            "id": "change_execute",
            "execute": [ExecuteObservation(command="export")],
        }
    )
    with_pending = empty.model_copy(update={"id": "change_pending"})
    for change_set in (empty, applied, with_file, with_execute, with_pending):
        await store.save(change_set)
    pending = PendingMutation(
        id="pending_1",
        change_set_id=with_pending.id,
        workspace_key=with_pending.workspace_key,
        relative_path="out.md",
        kind=FileChangeKind.CREATED,
        node_type=FileNodeType.FILE,
        before_hash=None,
        before_blob=None,
        before_mode=None,
        intended_after_hash="b" * 64,
        intended_after_blob="b" * 64,
        intended_after_mode=0o644,
        created_at=created_at,
    )
    await store.save_pending(pending)

    assert await store.delete_empty_open("change_missing") is False
    assert await store.delete_empty_open(applied.id) is False
    assert await store.delete_empty_open(with_file.id) is False
    assert await store.delete_empty_open(with_execute.id) is False
    assert await store.delete_empty_open(with_pending.id) is False
    assert await store.delete_empty_open(empty.id) is True

    assert await store.get(empty.id) is None
    assert await store.get(applied.id) == applied
    assert await store.get(with_file.id) == with_file
    assert await store.get(with_execute.id) == with_execute
    assert await store.get(with_pending.id) == with_pending
    await store.delete_pending(pending.id)
    assert await store.delete_empty_open(with_pending.id) is True


async def test_delete_empty_open_retains_a_tool_activity_reference(
    application_database: ApplicationSQLite,
) -> None:
    store = SQLiteChangeSetStore(application_database)
    created_at = datetime.now(UTC)
    change_set = ChangeSet(
        id="change_referenced",
        session_id="session_1",
        turn_id=None,
        workspace_key="ws_1",
        lifecycle=ChangeLifecycle.OPEN,
        reversibility=ChangeReversibility.FULL,
        created_at=created_at,
    )
    await store.save(change_set)

    def write_reference(connection: sqlite3.Connection) -> None:
        timestamp = created_at.isoformat()
        connection.execute(
            "INSERT INTO threads ("
            "thread_id, workspace_key, title, title_source, current_model, "
            "thinking_enabled, skill_mode, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "thread_1",
                "ws_1",
                "Thread",
                "manual",
                None,
                0,
                "off",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO tool_activities ("
            "activity_id, thread_id, turn_id, operation_id, call_id, sequence, "
            "origin, tool_name, outcome, input_summary, result_summary, "
            "error_code, duration_ms, change_set_id, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "activity_1",
                "thread_1",
                None,
                "operation_1",
                "call_1",
                1,
                "direct",
                "write_file",
                "success",
                "input",
                "result",
                None,
                0,
                change_set.id,
                timestamp,
            ),
        )

    await application_database.write(write_reference)

    assert await store.delete_empty_open(change_set.id) is False
    assert await store.get(change_set.id) == change_set


async def test_file_change_mutation_identity_survives_reopen(
    application_database: ApplicationSQLite,
) -> None:
    created_at = datetime.now(UTC)
    change_set = ChangeSet(
        id="change_1",
        session_id="session_1",
        turn_id="turn_1",
        workspace_key="ws_1",
        lifecycle=ChangeLifecycle.OPEN,
        reversibility=ChangeReversibility.FULL,
        files=[
            FileChange(
                mutation_id="operation_1",
                path="src/node",
                kind=FileChangeKind.UPDATED,
                node_type=FileNodeType.FILE,
                before_node_type=FileNodeType.DIRECTORY,
                after_node_type=FileNodeType.FILE,
                before_hash="b" * 64,
                after_hash="a" * 64,
                after_blob="a" * 64,
                before_mode=0o755,
                after_mode=0o644,
            )
        ],
        created_at=created_at,
    )

    await SQLiteChangeSetStore(application_database).save(change_set)

    reopened = await SQLiteChangeSetStore(application_database).get(change_set.id)
    assert reopened == change_set
    assert reopened is not None
    assert reopened.files[0].mutation_id == "operation_1"
    assert reopened.files[0].before_node_type is FileNodeType.DIRECTORY
    assert reopened.files[0].after_node_type is FileNodeType.FILE


def test_schema_seven_file_change_without_optional_recovery_fields_is_readable() -> (
    None
):
    legacy = FileChange.model_validate(
        {
            "path": "src/app.py",
            "kind": FileChangeKind.UPDATED,
            "node_type": FileNodeType.FILE,
            "before_hash": "a" * 64,
            "after_hash": "b" * 64,
            "before_blob": "a" * 64,
            "after_blob": "b" * 64,
        }
    )

    assert legacy.mutation_id is None
    assert legacy.before_node_type is None
    assert legacy.after_node_type is None
    assert legacy.resolved_before_node_type is FileNodeType.FILE
    assert legacy.resolved_after_node_type is FileNodeType.FILE
