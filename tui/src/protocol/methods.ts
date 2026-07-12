import { z } from "zod";

import {
  applicationResultSchema,
  boundedText,
  jsonValueSchema,
  productErrorSchema,
  safeIntegerSchema,
  utcTimestampSchema,
} from "./base.js";
import { commandIntentSchema, commandResultSchema } from "./commands.js";

const nonNegativeIntegerSchema = safeIntegerSchema.min(0);
const positiveIntegerSchema = safeIntegerSchema.min(1);
const emptyParamsSchema = z.strictObject({});
const identifierSchema = boundedText(1, 128);

export const workspacePresentationSchema = z.strictObject({
  display_path: boundedText(1, 4_096),
  branch: boundedText(1, 255).optional(),
});

export const initializeParamsSchema = z.strictObject({
  protocol_version: safeIntegerSchema,
  client_name: boundedText(1, 128),
  client_version: boundedText(1, 64),
});

export const initializeResultSchema = z.strictObject({
  product_version: boundedText(1, 64),
  protocol_version: z.literal(1),
  status: z.enum(["ready", "trust_required"]),
  session_id: identifierSchema,
  interaction_id: identifierSchema.optional(),
  workspace: workspacePresentationSchema,
  capabilities: z.array(z.string()),
});

const secretStatusSchema = z.strictObject({
  deepseek_api_key: z.boolean(),
  moonshot_api_key: z.boolean(),
  mem0_api_key: z.boolean(),
});

export const credentialSourceSchema = z.enum([
  "missing",
  "user_env_file",
  "process_environment",
]);
export const providerCredentialStatusSchema = z.strictObject({
  provider: z.enum(["deepseek", "kimi"]),
  environment_variable: boundedText(1, 128),
  source: credentialSourceSchema,
  mutable: z.boolean(),
});
export const providerCredentialStatusesSchema = z.strictObject({
  deepseek: providerCredentialStatusSchema,
  kimi: providerCredentialStatusSchema,
});

export const applicationStateSchema = z.strictObject({
  initialized: z.boolean(),
  session_id: identifierSchema,
  workspace_key: identifierSchema,
  workspace: workspacePresentationSchema,
  workspace_trusted: z.boolean(),
  current_thread_id: identifierSchema.optional(),
  current_model: boundedText(0, 200).optional(),
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
  usage: z.record(z.string(), nonNegativeIntegerSchema),
  configuration_diagnostics: z.array(z.string()),
});

export const threadSchema = z.strictObject({
  id: identifierSchema,
  workspace_key: identifierSchema,
  title: boundedText(1, 500),
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
    metadata: z.record(z.string(), jsonValueSchema),
    created_at: utcTimestampSchema,
  })
  .superRefine(({ kind, content }, context) => {
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

export const usageSummarySchema = z.strictObject({
  input_tokens: nonNegativeIntegerSchema,
  output_tokens: nonNegativeIntegerSchema,
  reasoning_tokens: nonNegativeIntegerSchema,
  cache_read_tokens: nonNegativeIntegerSchema,
  cache_write_tokens: nonNegativeIntegerSchema,
  model_calls: nonNegativeIntegerSchema,
  tool_calls: nonNegativeIntegerSchema,
  provider_retries: nonNegativeIntegerSchema,
  compressions: nonNegativeIntegerSchema,
  active_execution_seconds: z.number().finite().min(0),
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

export const changeSetSummarySchema = z.strictObject({
  change_set_id: identifierSchema,
  turn_id: identifierSchema.optional(),
  operation_id: identifierSchema.optional(),
  lifecycle: boundedText(1, 64),
  changed_paths: z.array(z.string()).max(1_000),
  reversibility: boundedText(1, 64),
  created_at: utcTimestampSchema,
  sealed_at: utcTimestampSchema.optional(),
});

const threadListParamsSchema = z.strictObject({
  cursor: boundedText(1, 1_024).optional(),
  limit: positiveIntegerSchema.max(200).optional(),
});
const threadListResultSchema = z.strictObject({
  threads: z.array(threadSchema),
  has_more: z.boolean(),
  next_cursor: boundedText(1, 1_024).optional(),
});
const threadReadParamsSchema = z.strictObject({
  thread_id: identifierSchema,
  before_sequence: positiveIntegerSchema.optional(),
  limit: positiveIntegerSchema.max(500).optional(),
});
const threadReadResultSchema = z.strictObject({
  view: threadViewSchema,
  change_sets: z.array(changeSetSummarySchema),
  has_more: z.boolean(),
  next_before_sequence: positiveIntegerSchema.optional(),
});
const operationAcceptedSchema = z.strictObject({
  operation_id: identifierSchema,
  thread_id: identifierSchema.optional(),
  turn_id: identifierSchema.optional(),
});
const interactionResultSchema = z.strictObject({
  accepted: z.boolean(),
  status: boundedText(1, 128),
  error: productErrorSchema.optional(),
});
const cancelResultSchema = z.strictObject({
  operation_id: identifierSchema,
  cancelled: z.boolean(),
});
const shutdownResultSchema = z.strictObject({ stopped: z.literal(true) });
export const providerCredentialSetResultSchema = z.strictObject({
  provider: z.enum(["deepseek", "kimi"]),
  status: z.enum(["saved", "invalid", "confirm_unverified"]),
  source: credentialSourceSchema,
  code: boundedText(1, 128),
});

export const methodSchemas = {
  initialize: {
    params: initializeParamsSchema,
    value: initializeResultSchema,
    result: applicationResultSchema(initializeResultSchema),
  },
  "application.getState": {
    params: emptyParamsSchema,
    value: applicationStateSchema,
    result: applicationResultSchema(applicationStateSchema),
  },
  "thread.list": {
    params: threadListParamsSchema,
    value: threadListResultSchema,
    result: applicationResultSchema(threadListResultSchema),
  },
  "thread.read": {
    params: threadReadParamsSchema,
    value: threadReadResultSchema,
    result: applicationResultSchema(threadReadResultSchema),
  },
  "turn.submit": {
    params: z.strictObject({
      thread_id: identifierSchema,
      content: boundedText(1, 200_000),
    }),
    value: operationAcceptedSchema,
    result: applicationResultSchema(operationAcceptedSchema),
  },
  "direct.execute": {
    params: z.strictObject({
      thread_id: identifierSchema,
      command: boundedText(1, 30_000),
    }),
    value: operationAcceptedSchema,
    result: applicationResultSchema(operationAcceptedSchema),
  },
  "command.execute": {
    params: commandIntentSchema,
    value: commandResultSchema,
    result: applicationResultSchema(commandResultSchema),
  },
  "provider.credential.set": {
    params: z.strictObject({
      provider: z.enum(["deepseek", "kimi"]),
      api_key: boundedText(1, 20_000),
      allow_unverified: z.boolean().optional(),
    }),
    value: providerCredentialSetResultSchema,
    result: applicationResultSchema(providerCredentialSetResultSchema),
  },
  "interaction.respond": {
    params: z.strictObject({
      interaction_id: identifierSchema,
      decision: boundedText(1, 128),
    }),
    value: interactionResultSchema,
    result: applicationResultSchema(interactionResultSchema),
  },
  "operation.cancel": {
    params: z.strictObject({ operation_id: identifierSchema }),
    value: cancelResultSchema,
    result: applicationResultSchema(cancelResultSchema),
  },
  shutdown: {
    params: emptyParamsSchema,
    value: shutdownResultSchema,
    result: applicationResultSchema(shutdownResultSchema),
  },
} as const;

export const methodNames = Object.keys(
  methodSchemas,
) as (keyof typeof methodSchemas)[];
export type MethodName = keyof typeof methodSchemas;
export type MethodParams = {
  [Method in MethodName]: z.infer<(typeof methodSchemas)[Method]["params"]>;
};
export type MethodValue = {
  [Method in MethodName]: z.infer<(typeof methodSchemas)[Method]["value"]>;
};
