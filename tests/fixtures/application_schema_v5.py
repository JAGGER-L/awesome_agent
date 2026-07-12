from __future__ import annotations

import sqlite3
from pathlib import Path

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

_MIGRATION_3_PREFIX = """
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
"""

_V10_THREAD_ENTRIES = """
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
"""

_V11_THREAD_ENTRIES = """
CREATE TABLE thread_entries (
    entry_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    kind TEXT NOT NULL CHECK (
        kind IN ('user_message', 'assistant_message', 'direct_command')
    ),
    content TEXT NOT NULL,
    client_message_id TEXT,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (
        (kind = 'user_message' AND client_message_id IS NOT NULL)
        OR (kind <> 'user_message' AND client_message_id IS NULL)
    ),
    UNIQUE (thread_id, entry_id),
    UNIQUE (thread_id, client_message_id)
);
"""

_MIGRATION_3_SUFFIX = """
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

_MIGRATION_4 = """
CREATE TABLE mcp_enablements (
    workspace_key TEXT NOT NULL,
    server_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    enabled_at TEXT NOT NULL,
    PRIMARY KEY (workspace_key, server_id)
)
"""

_MIGRATION_5 = """
DROP INDEX idx_threads_workspace_updated;
CREATE INDEX idx_threads_workspace_updated
ON threads (workspace_key, updated_at DESC, thread_id);
CREATE INDEX idx_tool_activities_thread_turn
ON tool_activities (thread_id, turn_id);
CREATE INDEX idx_tool_activities_thread_operation
ON tool_activities (thread_id, operation_id)
"""


def create_v10_schema_v5(path: Path) -> None:
    _create_schema_v5(path, thread_entries=_V10_THREAD_ENTRIES)


def create_v11_schema_v5(path: Path) -> None:
    _create_schema_v5(path, thread_entries=_V11_THREAD_ENTRIES)


def _create_schema_v5(path: Path, *, thread_entries: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    migration_3 = _MIGRATION_3_PREFIX + thread_entries + _MIGRATION_3_SUFFIX
    migrations = (_MIGRATION_1, _MIGRATION_2, migration_3, _MIGRATION_4, _MIGRATION_5)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for version, migration in enumerate(migrations, start=1):
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{migration.rstrip().rstrip(';')};\n"
                f"PRAGMA user_version = {version};\n"
                "COMMIT;"
            )
        _seed_released_state(
            connection,
            include_client_identity=thread_entries == _V11_THREAD_ENTRIES,
        )


def _seed_released_state(
    connection: sqlite3.Connection,
    *,
    include_client_identity: bool,
) -> None:
    connection.execute(
        "INSERT INTO trusted_workspaces VALUES (?, ?, ?)",
        ("workspace_existing", "E:/release-smoke", "2026-07-12T00:00:00+00:00"),
    )
    connection.execute(
        "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "thread_existing",
            "workspace_existing",
            "Existing",
            "deepseek/deepseek-v4-pro",
            0,
            "auto",
            "2026-07-12T00:00:00+00:00",
            "2026-07-12T00:00:01+00:00",
        ),
    )
    columns = "" if not include_client_identity else ", client_message_id"
    placeholders = "" if not include_client_identity else ", ?"
    connection.execute(
        f"""
        INSERT INTO thread_entries (
            entry_id, thread_id, sequence, kind, content,
            metadata_json, created_at{columns}
        ) VALUES (?, ?, ?, ?, ?, ?, ?{placeholders})
        """,
        (
            "entry_user",
            "thread_existing",
            1,
            "user_message",
            "hello",
            "{}",
            "2026-07-12T00:00:00+00:00",
            *(("client_existing",) if include_client_identity else ()),
        ),
    )
    connection.execute(
        f"""
        INSERT INTO thread_entries (
            entry_id, thread_id, sequence, kind, content,
            metadata_json, created_at{columns}
        ) VALUES (?, ?, ?, ?, ?, ?, ?{placeholders})
        """,
        (
            "entry_assistant",
            "thread_existing",
            2,
            "assistant_message",
            "hello back",
            "{}",
            "2026-07-12T00:00:01+00:00",
            *((None,) if include_client_identity else ()),
        ),
    )
    connection.execute(
        """
        INSERT INTO turns VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            "turn_existing",
            "thread_existing",
            "turn_existing",
            "completed",
            "deepseek",
            "deepseek/deepseek-v4-pro",
            0,
            "auto",
            "{}",
            "entry_user",
            "entry_assistant",
            "{}",
            "completed",
            None,
            "[]",
            "2026-07-12T00:00:00+00:00",
            "2026-07-12T00:00:01+00:00",
            "2026-07-12T00:00:01+00:00",
        ),
    )
