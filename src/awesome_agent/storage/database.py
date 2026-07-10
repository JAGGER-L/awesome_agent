from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

APPLICATION_SCHEMA_VERSION = 3

_MIGRATION_1 = """
CREATE TABLE trusted_workspaces (
    workspace_key TEXT PRIMARY KEY,
    canonical_path TEXT NOT NULL,
    trusted_at TEXT NOT NULL
)
"""

_MIGRATION_2 = """
CREATE TABLE change_sets (
    change_set_id TEXT PRIMARY KEY,
    workspace_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    lifecycle TEXT NOT NULL,
    reversibility TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sealed_at TEXT
);
CREATE INDEX idx_change_sets_workspace_created
ON change_sets (workspace_key, created_at DESC);

CREATE TABLE pending_mutations (
    pending_id TEXT PRIMARY KEY,
    change_set_id TEXT NOT NULL REFERENCES change_sets(change_set_id),
    relative_path TEXT NOT NULL,
    kind TEXT NOT NULL,
    node_type TEXT NOT NULL,
    before_hash TEXT,
    before_blob TEXT,
    before_mode INTEGER,
    intended_after_hash TEXT,
    intended_after_blob TEXT,
    intended_after_mode INTEGER,
    created_at TEXT NOT NULL
);
"""

_MIGRATION_3 = """
CREATE TABLE threads (
    thread_id TEXT PRIMARY KEY,
    workspace_key TEXT NOT NULL,
    title TEXT NOT NULL,
    current_model TEXT,
    thinking_enabled INTEGER NOT NULL CHECK (thinking_enabled IN (0, 1)),
    skill_mode TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_threads_workspace_updated
ON threads (workspace_key, updated_at DESC);

CREATE TABLE thread_entries (
    entry_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    kind TEXT NOT NULL CHECK (
        kind IN ('user_message', 'assistant_message', 'direct_command')
    ),
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (thread_id, entry_id)
);
CREATE UNIQUE INDEX idx_thread_entries_sequence
ON thread_entries (thread_id, sequence);

CREATE TABLE turns (
    turn_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
    checkpoint_key TEXT NOT NULL UNIQUE CHECK (checkpoint_key = turn_id),
    status TEXT NOT NULL CHECK (
        status IN ('in_progress', 'completed', 'failed', 'cancelled')
    ),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    thinking_enabled INTEGER NOT NULL CHECK (thinking_enabled IN (0, 1)),
    skill_mode TEXT NOT NULL,
    budgets_json TEXT NOT NULL,
    user_entry_id TEXT NOT NULL,
    assistant_entry_id TEXT,
    usage_json TEXT NOT NULL,
    termination_reason TEXT,
    error_code TEXT,
    context_manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (thread_id, turn_id),
    FOREIGN KEY (thread_id, user_entry_id)
        REFERENCES thread_entries(thread_id, entry_id),
    FOREIGN KEY (thread_id, assistant_entry_id)
        REFERENCES thread_entries(thread_id, entry_id)
);
CREATE UNIQUE INDEX idx_turns_one_in_progress
ON turns (thread_id) WHERE status = 'in_progress';
CREATE INDEX idx_turns_thread_created
ON turns (thread_id, created_at);

CREATE TABLE thread_summaries (
    thread_id TEXT PRIMARY KEY REFERENCES threads(thread_id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    covered_entry_sequence INTEGER NOT NULL CHECK (covered_entry_sequence >= 0),
    covered_turn_count INTEGER NOT NULL CHECK (covered_turn_count >= 0),
    estimated_tokens INTEGER NOT NULL CHECK (estimated_tokens >= 0),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE tool_activities (
    activity_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
    turn_id TEXT,
    operation_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    origin TEXT NOT NULL CHECK (origin IN ('agent', 'direct')),
    tool_name TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'error', 'cancelled')),
    input_summary TEXT NOT NULL,
    result_summary TEXT NOT NULL,
    error_code TEXT,
    duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
    change_set_id TEXT REFERENCES change_sets(change_set_id),
    created_at TEXT NOT NULL,
    CHECK (
        (origin = 'agent' AND turn_id IS NOT NULL)
        OR (origin = 'direct' AND turn_id IS NULL)
    ),
    FOREIGN KEY (thread_id, turn_id) REFERENCES turns(thread_id, turn_id)
);
CREATE UNIQUE INDEX idx_tool_activities_operation_call
ON tool_activities (operation_id, call_id);
CREATE INDEX idx_tool_activities_thread_created
ON tool_activities (thread_id, created_at);
"""

_MIGRATIONS: dict[int, str] = {
    1: _MIGRATION_1,
    2: _MIGRATION_2,
    3: _MIGRATION_3,
}


class ApplicationSchemaTooNew(RuntimeError):
    def __init__(self, *, found: int, supported: int) -> None:
        super().__init__(
            f"Application state schema {found} is newer than supported {supported}."
        )
        self.found = found
        self.supported = supported


def _connect(path: Path) -> sqlite3.Connection:
    database_path = path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize_application_database(path: Path) -> None:
    connection = _connect(path)
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > APPLICATION_SCHEMA_VERSION:
            raise ApplicationSchemaTooNew(
                found=version,
                supported=APPLICATION_SCHEMA_VERSION,
            )
        for target_version in range(version + 1, APPLICATION_SCHEMA_VERSION + 1):
            migration = _MIGRATIONS[target_version].rstrip().rstrip(";")
            script = (
                "BEGIN IMMEDIATE;\n"
                f"{migration};\n"
                f"PRAGMA user_version = {target_version};\n"
                "COMMIT;"
            )
            try:
                connection.executescript(script)
            except sqlite3.Error:
                if connection.in_transaction:
                    connection.rollback()
                raise
    finally:
        connection.close()


@contextmanager
def application_connection(path: Path) -> Iterator[sqlite3.Connection]:
    initialize_application_database(path)
    connection = _connect(path)
    try:
        yield connection
    finally:
        connection.close()
