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
| `revision` | `INTEGER` | no |
| `created_at` | `DATETIME` | no |
| `updated_at` | `DATETIME` | no |

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

## `context_compactions`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `run_id` | `UUID` | no |
| `agent_id` | `UUID` | yes |
| `runtime_route` | `VARCHAR(128)` | no |
| `before_estimated_tokens` | `INTEGER` | no |
| `after_estimated_tokens` | `INTEGER` | no |
| `summary` | `TEXT` | no |
| `artifact_refs` | `JSONB` | no |
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

## `model_calls`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `run_id` | `UUID` | no |
| `agent_id` | `UUID` | yes |
| `turn` | `INTEGER` | no |
| `provider` | `VARCHAR(64)` | no |
| `model` | `VARCHAR(128)` | no |
| `status` | `VARCHAR(64)` | no |
| `stop_reason` | `VARCHAR(64)` | yes |
| `input_tokens` | `INTEGER` | yes |
| `output_tokens` | `INTEGER` | yes |
| `reasoning_tokens` | `INTEGER` | yes |
| `cache_read_tokens` | `INTEGER` | yes |
| `cache_write_tokens` | `INTEGER` | yes |
| `latency_ms` | `INTEGER` | yes |
| `estimated_cost_usd` | `FLOAT` | yes |
| `trace_id` | `VARCHAR(64)` | yes |
| `span_id` | `VARCHAR(32)` | yes |
| `error` | `TEXT` | yes |
| `created_at` | `DATETIME` | no |

## `observability_metrics`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `run_id` | `UUID` | yes |
| `name` | `VARCHAR(255)` | no |
| `value` | `FLOAT` | no |
| `unit` | `VARCHAR(32)` | no |
| `attributes` | `JSONB` | no |
| `created_at` | `DATETIME` | no |

## `observability_spans`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `run_id` | `UUID` | no |
| `trace_id` | `VARCHAR(64)` | no |
| `span_id` | `VARCHAR(32)` | no |
| `parent_span_id` | `VARCHAR(32)` | yes |
| `name` | `VARCHAR(255)` | no |
| `category` | `VARCHAR(64)` | no |
| `status` | `VARCHAR(64)` | no |
| `started_at` | `DATETIME` | no |
| `ended_at` | `DATETIME` | yes |
| `duration_ms` | `INTEGER` | yes |
| `attributes` | `JSONB` | no |
| `error` | `TEXT` | yes |

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

## `run_budget_ledgers`

| Column | Type | Nullable |
| --- | --- | --- |
| `run_id` | `UUID` | no |
| `total_input_tokens` | `INTEGER` | no |
| `total_output_tokens` | `INTEGER` | no |
| `total_reasoning_tokens` | `INTEGER` | no |
| `active_seconds` | `INTEGER` | no |
| `model_call_count` | `INTEGER` | no |
| `threshold_status` | `VARCHAR(64)` | no |
| `active_window_started_at` | `DATETIME` | yes |
| `created_at` | `DATETIME` | no |
| `updated_at` | `DATETIME` | no |

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
| `parent_run_id` | `UUID` | yes |
| `root_run_id` | `UUID` | yes |
| `depth` | `INTEGER` | no |
| `child_role` | `VARCHAR(64)` | yes |
| `runtime_route` | `VARCHAR(128)` | yes |
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
| `cancel_requested_at` | `DATETIME` | yes |
| `cancel_requested_by` | `VARCHAR(255)` | yes |
| `cancel_reason` | `TEXT` | yes |
| `result_text` | `TEXT` | yes |
| `workspace_path` | `TEXT` | yes |
| `integration_branch` | `VARCHAR(255)` | yes |
| `workspace_state` | `VARCHAR(32)` | yes |
| `workspace_retention_status` | `VARCHAR(32)` | no |
| `workspace_cleaned_at` | `DATETIME` | yes |
| `workspace_cleanup_reason` | `TEXT` | yes |
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

## `team_assignments`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `root_run_id` | `UUID` | no |
| `parent_run_id` | `UUID` | no |
| `child_run_id` | `UUID` | no |
| `kind` | `VARCHAR(32)` | no |
| `status` | `VARCHAR(32)` | no |
| `role_profile` | `VARCHAR(128)` | no |
| `runtime_route` | `VARCHAR(128)` | no |
| `goal` | `TEXT` | no |
| `allowed_tools` | `JSONB` | no |
| `deferred_tools` | `JSONB` | no |
| `promoted_tools` | `JSONB` | no |
| `allowed_skills` | `JSONB` | no |
| `can_write` | `BOOLEAN` | no |
| `can_delegate` | `BOOLEAN` | no |
| `max_subagents` | `INTEGER` | no |
| `acceptance_criteria` | `JSONB` | no |
| `handoff_context` | `JSONB` | no |
| `retire_reason` | `TEXT` | yes |
| `created_at` | `DATETIME` | no |
| `updated_at` | `DATETIME` | no |

## `team_child_results`

| Column | Type | Nullable |
| --- | --- | --- |
| `child_run_id` | `UUID` | no |
| `assignment_id` | `UUID` | no |
| `parent_run_id` | `UUID` | no |
| `root_run_id` | `UUID` | no |
| `status` | `VARCHAR(32)` | no |
| `summary` | `TEXT` | no |
| `patch_artifact_id` | `UUID` | yes |
| `changed_files` | `JSONB` | no |
| `evidence_artifact_refs` | `JSONB` | no |
| `failure_kind` | `VARCHAR(64)` | yes |
| `patch_aggregated` | `BOOLEAN` | no |
| `created_at` | `DATETIME` | no |
| `updated_at` | `DATETIME` | no |

## `team_mailbox_messages`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `team_root_run_id` | `UUID` | no |
| `sender_run_id` | `UUID` | no |
| `sender_agent_id` | `UUID` | yes |
| `recipient_run_id` | `UUID` | no |
| `recipient_agent_id` | `UUID` | yes |
| `route` | `VARCHAR(64)` | no |
| `message_type` | `VARCHAR(64)` | no |
| `status` | `VARCHAR(32)` | no |
| `subject` | `VARCHAR(512)` | no |
| `body_summary` | `TEXT` | no |
| `artifact_refs` | `JSONB` | no |
| `requires_response` | `BOOLEAN` | no |
| `response_to_message_id` | `UUID` | yes |
| `created_at` | `DATETIME` | no |
| `read_at` | `DATETIME` | yes |
| `responded_at` | `DATETIME` | yes |

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

## `validation_gate_results`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `report_id` | `UUID` | no |
| `run_id` | `UUID` | no |
| `gate_id` | `VARCHAR(128)` | no |
| `name` | `VARCHAR(255)` | no |
| `command` | `JSONB` | no |
| `required` | `BOOLEAN` | no |
| `status` | `VARCHAR(32)` | no |
| `exit_code` | `INTEGER` | yes |
| `duration_ms` | `INTEGER` | yes |
| `stdout_summary` | `TEXT` | no |
| `stderr_summary` | `TEXT` | no |
| `artifact_refs` | `JSONB` | no |
| `failure_kind` | `VARCHAR(64)` | yes |
| `created_at` | `DATETIME` | no |

## `validation_reports`

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no |
| `run_id` | `UUID` | no |
| `agent_id` | `UUID` | yes |
| `attempt` | `INTEGER` | no |
| `status` | `VARCHAR(32)` | no |
| `summary` | `TEXT` | no |
| `created_at` | `DATETIME` | no |

## `worker_heartbeats`

| Column | Type | Nullable |
| --- | --- | --- |
| `worker_id` | `UUID` | no |
| `worker_name` | `VARCHAR(255)` | no |
| `started_at` | `DATETIME` | no |
| `heartbeat_at` | `DATETIME` | no |
| `supported_runtime_routes` | `JSONB` | no |
| `status` | `VARCHAR(32)` | no |
