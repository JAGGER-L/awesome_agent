import { z } from "zod";

import {
  boundedText,
  jsonValueSchema,
  safeIntegerSchema,
  utcTimestampSchema,
} from "./base.js";
import { modelIdentitySchema } from "./identity.js";

const nonNegativeIntegerSchema = safeIntegerSchema.min(0);
const positiveIntegerSchema = safeIntegerSchema.min(1);
const identifierSchema = boundedText(1, 128);
const clientMessageIdentifierSchema = identifierSchema.regex(
  /^client_[A-Za-z0-9_-]+$/,
);

export const usageSummarySchema = z.strictObject({
  input_tokens: safeIntegerSchema.min(0),
  output_tokens: safeIntegerSchema.min(0),
  reasoning_tokens: safeIntegerSchema.min(0),
  cache_read_tokens: safeIntegerSchema.min(0),
  cache_write_tokens: safeIntegerSchema.min(0),
  model_calls: safeIntegerSchema.min(0),
  tool_calls: safeIntegerSchema.min(0),
  provider_retries: safeIntegerSchema.min(0),
  compressions: safeIntegerSchema.min(0),
  active_execution_seconds: z.number().finite().min(0),
});

export const credentialSourceSchema = z.enum(["environment", "awesome"]);
export const providerCredentialStatusSchema = z.strictObject({
  provider: z.enum(["deepseek", "kimi", "mem0"]),
  environment_variable: boundedText(1, 128),
  environment_configured: z.boolean(),
  awesome_configured: z.boolean(),
  selected_source: credentialSourceSchema.nullable().optional(),
});
export const providerCredentialStatusesSchema = z.strictObject({
  deepseek: providerCredentialStatusSchema,
  kimi: providerCredentialStatusSchema,
  mem0: providerCredentialStatusSchema,
});

export const workspacePresentationSchema = z.strictObject({
  display_path: boundedText(1, 4_096),
  branch: boundedText(1, 255).optional(),
});

const secretStatusSchema = z.strictObject({
  deepseek_api_key: z.boolean(),
  moonshot_api_key: z.boolean(),
  mem0_api_key: z.boolean(),
});

export const applicationStateSchema = z.strictObject({
  initialized: z.boolean(),
  session_id: identifierSchema,
  workspace_key: identifierSchema,
  workspace: workspacePresentationSchema,
  workspace_trusted: z.boolean(),
  current_thread_id: identifierSchema.optional(),
  model_identity: modelIdentitySchema.optional(),
  thinking_enabled: z.boolean(),
  skill_mode: boundedText(1, 64),
  active_operation_id: identifierSchema.optional(),
  pending_interaction_id: identifierSchema.optional(),
  permission_mode: z.enum(["request_approval", "full_access"]),
  configuration_valid: z.boolean(),
  secret_status: secretStatusSchema,
  provider_credentials: providerCredentialStatusesSchema,
  memory_status: z.record(z.string(), jsonValueSchema),
  mcp_status: z.array(z.record(z.string(), jsonValueSchema)),
  usage: z.record(z.string(), z.number().finite().min(0)),
  configuration_diagnostics: z.array(z.string()),
});

export const threadSchema = z.strictObject({
  id: identifierSchema,
  workspace_key: identifierSchema,
  title: boundedText(1, 500),
  title_source: z.enum(["automatic", "manual"]),
  current_model: boundedText(0, 200).optional(),
  thinking_enabled: z.boolean(),
  skill_mode: boundedText(1, 64),
  created_at: utcTimestampSchema,
  updated_at: utcTimestampSchema,
});

export const threadEntrySchema = z
  .strictObject({
    id: identifierSchema,
    thread_id: identifierSchema,
    sequence: positiveIntegerSchema,
    kind: z.enum(["user_message", "assistant_message", "direct_command"]),
    content: boundedText(0, 200_000),
    client_message_id: clientMessageIdentifierSchema.optional(),
    metadata: z.record(z.string(), jsonValueSchema),
    created_at: utcTimestampSchema,
  })
  .superRefine(({ kind, content, client_message_id }, context) => {
    if ((kind === "user_message") !== (client_message_id !== undefined)) {
      context.addIssue({
        code: "custom",
        message: "User message identity and entry kind disagree",
      });
    }
    if (kind === "direct_command" && Array.from(content).length > 30_000) {
      context.addIssue({
        code: "custom",
        message: "Direct command exceeds 30000 code points",
      });
    }
  });

export const budgetSchema = z.strictObject({
  model_calls: positiveIntegerSchema.max(256),
  tool_calls: positiveIntegerSchema.max(512),
  provider_retries: nonNegativeIntegerSchema.max(6),
  compressions: nonNegativeIntegerSchema.max(10),
  active_execution_seconds: positiveIntegerSchema.max(21_600),
  total_context_tokens: positiveIntegerSchema,
});

export const turnSchema = z
  .strictObject({
    id: identifierSchema,
    thread_id: identifierSchema,
    checkpoint_key: identifierSchema,
    status: z.enum(["in_progress", "completed", "failed", "cancelled"]),
    provider: boundedText(1, 64),
    model: boundedText(1, 200),
    thinking_enabled: z.boolean(),
    skill_mode: boundedText(1, 64),
    budgets: budgetSchema,
    user_entry_id: identifierSchema,
    assistant_entry_id: identifierSchema.optional(),
    usage: usageSummarySchema,
    termination_reason: boundedText(0, 128).optional(),
    error_code: boundedText(0, 128).optional(),
    context_manifest: z.array(z.record(z.string(), jsonValueSchema)),
    created_at: utcTimestampSchema,
    updated_at: utcTimestampSchema,
    completed_at: utcTimestampSchema.optional(),
  })
  .superRefine((turn, context) => {
    if (turn.checkpoint_key !== turn.id) {
      context.addIssue({
        code: "custom",
        message: "checkpoint_key must equal id",
      });
    }
    if (
      turn.status === "in_progress" &&
      (turn.completed_at || turn.assistant_entry_id || turn.error_code)
    ) {
      context.addIssue({
        code: "custom",
        message: "Invalid in-progress Turn shape",
      });
    }
    if (turn.status !== "in_progress" && !turn.completed_at) {
      context.addIssue({
        code: "custom",
        message: "Terminal Turn requires completed_at",
      });
    }
    if (
      turn.status === "completed" &&
      (!turn.assistant_entry_id || turn.error_code)
    ) {
      context.addIssue({
        code: "custom",
        message: "Invalid completed Turn shape",
      });
    }
    if (turn.status === "failed" && !turn.error_code) {
      context.addIssue({
        code: "custom",
        message: "Failed Turn requires error_code",
      });
    }
  });

export const threadSummarySchema = z.strictObject({
  thread_id: identifierSchema,
  content: boundedText(0, 200_000),
  content_hash: z.string().regex(/^[a-f0-9]{64}$/),
  covered_entry_sequence: nonNegativeIntegerSchema,
  covered_turn_count: nonNegativeIntegerSchema,
  estimated_tokens: nonNegativeIntegerSchema,
  provider: boundedText(1, 64),
  model: boundedText(1, 200),
  updated_at: utcTimestampSchema,
});

export const toolActivitySchema = z
  .strictObject({
    id: identifierSchema,
    thread_id: identifierSchema,
    turn_id: identifierSchema.optional(),
    operation_id: identifierSchema,
    call_id: identifierSchema,
    sequence: positiveIntegerSchema,
    origin: z.enum(["agent", "direct"]),
    tool_name: boundedText(1, 200),
    outcome: z.enum(["success", "error", "cancelled"]),
    input_summary: boundedText(0, 2_000),
    result_summary: boundedText(0, 4_000),
    error_code: boundedText(0, 128).optional(),
    duration_ms: nonNegativeIntegerSchema,
    change_set_id: identifierSchema.optional(),
    created_at: utcTimestampSchema,
  })
  .superRefine(({ origin, turn_id }, context) => {
    if ((origin === "agent") !== (turn_id !== undefined)) {
      context.addIssue({
        code: "custom",
        message: "Tool origin and turn identity disagree",
      });
    }
  });

export const threadViewSchema = z.strictObject({
  thread: threadSchema,
  entries: z.array(threadEntrySchema),
  turns: z.array(turnSchema),
  summary: threadSummarySchema.optional(),
  tool_activities: z.array(toolActivitySchema),
});

export const changeDeltaSchema = z.discriminatedUnion("kind", [
  z.strictObject({
    kind: z.literal("text_file"),
    path: boundedText(1, 1_000),
    change_kind: z.enum(["created", "updated", "deleted"]),
    additions: nonNegativeIntegerSchema,
    deletions: nonNegativeIntegerSchema,
  }),
  z.strictObject({
    kind: z.literal("binary_file"),
    path: boundedText(1, 1_000),
    change_kind: z.enum(["created", "updated", "deleted"]),
    before_bytes: nonNegativeIntegerSchema,
    after_bytes: nonNegativeIntegerSchema,
  }),
  z.strictObject({
    kind: z.literal("directory"),
    path: boundedText(1, 1_000),
    change_kind: z.enum(["created", "updated", "deleted"]),
  }),
  z.strictObject({
    kind: z.literal("symlink"),
    path: boundedText(1, 1_000),
    change_kind: z.enum(["created", "updated", "deleted"]),
  }),
]);

export const changeSetSummarySchema = z.strictObject({
  change_set_id: identifierSchema,
  turn_id: identifierSchema.optional(),
  operation_id: identifierSchema.optional(),
  lifecycle: boundedText(1, 64),
  changes: z.array(changeDeltaSchema).max(1_000),
  created_at: utcTimestampSchema,
  sealed_at: utcTimestampSchema.optional(),
});

export const threadReadResultSchema = z.strictObject({
  view: threadViewSchema,
  change_sets: z.array(changeSetSummarySchema),
  has_more: z.boolean(),
  next_before_sequence: positiveIntegerSchema.optional(),
});
