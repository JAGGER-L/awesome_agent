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
| `dispatch_status` | `VARCHAR(32)` | no |
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
