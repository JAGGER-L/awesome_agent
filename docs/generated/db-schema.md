# Database Schema

Generated from SQLAlchemy metadata.

## `agents`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `run_id` | `UUID` | no |
| `parent_agent_id` | `UUID` | yes |
| `kind` | `VARCHAR(32)` | no |
| `profile` | `VARCHAR(128)` | no |
| `model` | `VARCHAR(128)` | no |
| `status` | `VARCHAR(32)` | no |
| `created_at` | `DATETIME` | no |

## `approvals`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `run_id` | `UUID` | no |
| `agent_id` | `UUID` | yes |
| `tool_invocation_id` | `UUID` | no |
| `tool_call_id` | `VARCHAR(255)` | no |
| `tool_name` | `VARCHAR(128)` | no |
| `tool_version` | `VARCHAR(32)` | no |
| `canonical_arguments` | `JSONB` | no |
| `arguments_hash` | `VARCHAR(64)` | no |
| `workspace_path` | `TEXT` | no |
| `workspace_fingerprint` | `VARCHAR(64)` | no |
| `capabilities` | `JSONB` | no |
| `risk_level` | `VARCHAR(32)` | no |
| `status` | `VARCHAR(32)` | no |
| `expires_at` | `DATETIME` | no |
| `decided_at` | `DATETIME` | yes |
| `decided_by` | `VARCHAR(255)` | yes |
| `decision_reason` | `TEXT` | yes |
| `created_at` | `DATETIME` | no |
| `updated_at` | `DATETIME` | no |

## `artifacts`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `run_id` | `UUID` | no |
| `agent_id` | `UUID` | yes |
| `artifact_type` | `VARCHAR(64)` | no |
| `path` | `TEXT` | no |
| `sha256` | `VARCHAR(64)` | no |
| `size` | `INTEGER` | no |
| `mime_type` | `VARCHAR(255)` | no |
| `summary` | `TEXT` | no |
| `created_at` | `DATETIME` | no |

## `intake_reservations`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `run_id` | `UUID` | no |
| `repository_id` | `UUID` | no |
| `base_commit` | `VARCHAR(64)` | no |
| `intent` | `VARCHAR(32)` | no |
| `workspace_path` | `TEXT` | no |
| `integration_branch` | `VARCHAR(255)` | no |
| `status` | `VARCHAR(32)` | no |
| `error` | `TEXT` | yes |
| `created_at` | `DATETIME` | no |
| `updated_at` | `DATETIME` | no |

## `repositories`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `root` | `TEXT` | no |
| `display_name` | `VARCHAR(255)` | no |
| `git_common_dir` | `TEXT` | no |
| `default_branch` | `VARCHAR(255)` | yes |
| `enabled` | `BOOLEAN` | no |
| `created_at` | `DATETIME` | no |
| `updated_at` | `DATETIME` | no |
| `last_seen_at` | `DATETIME` | no |

## `runs`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `goal` | `TEXT` | no |
| `mode` | `VARCHAR(32)` | no |
| `status` | `VARCHAR(32)` | no |
| `repository_id` | `UUID` | yes |
| `base_commit` | `VARCHAR(64)` | yes |
| `intent` | `VARCHAR(32)` | no |
| `execution_kind` | `VARCHAR(32)` | no |
| `graph_name` | `VARCHAR(128)` | yes |
| `graph_version` | `INTEGER` | yes |
| `dispatch_status` | `VARCHAR(32)` | no |
| `available_at` | `DATETIME` | no |
| `current_worker_id` | `UUID` | yes |
| `current_worker_name` | `VARCHAR(255)` | yes |
| `fencing_token` | `INTEGER` | no |
| `attempt` | `INTEGER` | no |
| `lease_acquired_at` | `DATETIME` | yes |
| `lease_expires_at` | `DATETIME` | yes |
| `heartbeat_at` | `DATETIME` | yes |
| `last_release_reason` | `TEXT` | yes |
| `last_dispatch_error` | `TEXT` | yes |
| `result_text` | `TEXT` | yes |
| `workspace_path` | `TEXT` | yes |
| `integration_branch` | `VARCHAR(255)` | yes |
| `workspace_state` | `VARCHAR(32)` | yes |
| `graph_thread_id` | `VARCHAR(128)` | yes |
| `legacy` | `BOOLEAN` | no |
| `created_at` | `DATETIME` | no |
| `updated_at` | `DATETIME` | no |

## `runtime_events`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `run_id` | `UUID` | no |
| `sequence` | `INTEGER` | no |
| `transition_id` | `VARCHAR(255)` | yes |
| `event_type` | `VARCHAR(128)` | no |
| `payload` | `JSONB` | no |
| `team_id` | `UUID` | yes |
| `agent_id` | `UUID` | yes |
| `parent_agent_id` | `UUID` | yes |
| `task_id` | `UUID` | yes |
| `trace_id` | `VARCHAR(64)` | yes |
| `span_id` | `VARCHAR(32)` | yes |
| `created_at` | `DATETIME` | no |

## `todos`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `run_id` | `UUID` | no |
| `parent_id` | `UUID` | yes |
| `title` | `VARCHAR(512)` | no |
| `description` | `TEXT` | no |
| `status` | `VARCHAR(32)` | no |
| `primary_owner_id` | `UUID` | yes |
| `collaborator_ids` | `JSONB` | no |
| `acceptance_criteria` | `JSONB` | no |
| `blocker` | `TEXT` | yes |
| `revision` | `INTEGER` | no |
| `created_at` | `DATETIME` | no |
| `updated_at` | `DATETIME` | no |

## `tool_invocations`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `run_id` | `UUID` | no |
| `agent_id` | `UUID` | yes |
| `tool_name` | `VARCHAR(128)` | no |
| `tool_version` | `VARCHAR(32)` | no |
| `status` | `VARCHAR(32)` | no |
| `idempotency_key` | `VARCHAR(255)` | no |
| `arguments_hash` | `VARCHAR(64)` | no |
| `risk_level` | `VARCHAR(32)` | no |
| `path_refs` | `JSONB` | no |
| `preimage_hashes` | `JSONB` | no |
| `expected_postimage_hashes` | `JSONB` | no |
| `result_summary` | `TEXT` | yes |
| `result_content` | `TEXT` | yes |
| `result_is_error` | `BOOLEAN` | no |
| `artifact_refs` | `JSONB` | no |
| `error` | `TEXT` | yes |
| `started_at` | `DATETIME` | yes |
| `completed_at` | `DATETIME` | yes |
| `created_at` | `DATETIME` | no |
| `updated_at` | `DATETIME` | no |
