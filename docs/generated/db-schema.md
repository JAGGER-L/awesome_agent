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
| `status` | `VARCHAR(32)` | no |
| `created_at` | `DATETIME` | no |

## `runs`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `goal` | `TEXT` | no |
| `mode` | `VARCHAR(32)` | no |
| `status` | `VARCHAR(32)` | no |
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
