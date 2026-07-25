import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from awesome_agent.core.changes import (
    ChangeLifecycle,
    ChangeReversibility,
    ChangeSet,
    FileChange,
    FileChangeKind,
    FileNodeType,
)
from awesome_agent.core.changes.errors import ChangeBlobCorrupt
from awesome_agent.core.changes.ports import PendingMutation
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore
from awesome_agent.storage.database import (
    APPLICATION_SCHEMA_VERSION,
    ApplicationSchemaMismatch,
    application_connection,
    initialize_application_database,
)


def test_current_schema_contains_change_and_pending_tables(tmp_path: Path) -> None:
    database = tmp_path / "application.db"
    initialize_application_database(database)
    with application_connection(database) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert APPLICATION_SCHEMA_VERSION == version == 7
    assert {"change_sets", "pending_mutations"} <= names


def test_schema_one_is_rejected_without_a_compatibility_migration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.db"
    with sqlite3.connect(database) as connection:
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

    with pytest.raises(ApplicationSchemaMismatch) as raised:
        initialize_application_database(database)

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


def test_change_set_and_pending_mutation_survive_reopen(tmp_path: Path) -> None:
    database = tmp_path / "application.db"
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

    first = SQLiteChangeSetStore(database)
    first.save(change_set)
    first.save_pending(pending)

    reopened = SQLiteChangeSetStore(database)
    assert reopened.get(change_set.id) == change_set
    assert reopened.latest("ws_1") == change_set
    assert reopened.list_pending() == [pending]
    reopened.delete_pending(pending.id)
    assert reopened.list_pending() == []


def test_pending_distinct_node_types_survive_reopen_without_a_schema_change(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.db"
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
    store = SQLiteChangeSetStore(database)
    store.save(change_set)
    store.save_pending(pending)

    reopened = SQLiteChangeSetStore(database)

    assert APPLICATION_SCHEMA_VERSION == 7
    assert reopened.list_pending() == [pending]
    assert reopened.list_pending()[0].before_node_type is FileNodeType.FILE
    assert reopened.list_pending()[0].intended_after_node_type is FileNodeType.DIRECTORY


def test_list_open_is_scoped_to_the_workspace(tmp_path: Path) -> None:
    store = SQLiteChangeSetStore(tmp_path / "application.db")
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
        store.save(change_set)

    assert store.list_open("ws_1") == [open_change]


def test_file_change_mutation_identity_survives_reopen(tmp_path: Path) -> None:
    database = tmp_path / "application.db"
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

    SQLiteChangeSetStore(database).save(change_set)

    reopened = SQLiteChangeSetStore(database).get(change_set.id)
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
