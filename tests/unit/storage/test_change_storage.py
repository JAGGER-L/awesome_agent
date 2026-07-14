import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from awesome_agent.core.changes import (
    ChangeLifecycle,
    ChangeReversibility,
    ChangeSet,
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
