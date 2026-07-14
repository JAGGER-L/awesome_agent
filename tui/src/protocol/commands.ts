import { z } from "zod";

import { boundedText, safeIntegerSchema } from "./base.js";
import { modelIdentitySchema } from "./identity.js";
import {
  applicationStateSchema,
  providerCredentialStatusesSchema,
  threadSchema,
  threadReadResultSchema,
  usageSummarySchema,
} from "./product-projections.js";

export const applicationCommandNames = [
  "new",
  "rename",
  "resume",
  "context",
  "compact",
  "auth",
  "model",
  "thinking",
  "workspace",
  "diff",
  "undo",
  "redo",
  "tools",
  "skills",
  "mcp",
  "memory",
  "status",
  "usage",
  "doctor",
  "config",
  "permissions",
] as const;

export const inkCommandNames = ["help", "theme", "copy", "quit"] as const;

export const commandNames = [
  ...applicationCommandNames,
  ...inkCommandNames,
] as const;

export const commandNameSchema = z.enum(commandNames);
export const commandOwnerSchema = z.enum(["application", "ink"]);

export type CommandName = z.infer<typeof commandNameSchema>;
export type CommandOwner = z.infer<typeof commandOwnerSchema>;

export const statusSnapshotSchema = z.strictObject({
  version: z.string().regex(/^\d+\.\d+\.\d+$/u),
  workspace_path: boundedText(1, 4_096),
  thread_title: boundedText(1, 500),
  thread_id: boundedText(1, 128),
  thread_display_id: boundedText(1, 128),
  model_identity: modelIdentitySchema,
  model_status: z.enum(["configured", "not_configured"]),
  thinking_enabled: z.boolean(),
  skill_mode: boundedText(1, 64),
  local_memory_enabled: z.boolean(),
  mem0_enabled: z.boolean(),
  mcp_ready: safeIntegerSchema.min(0),
  mcp_degraded: safeIntegerSchema.min(0),
  operation_status: z.enum(["idle", "active"]),
  operation_id: boundedText(1, 128).nullable().optional(),
  configuration_valid: z.boolean(),
  configuration_diagnostic_count: safeIntegerSchema.min(0),
  permission_mode: z.enum(["request_approval", "full_access"]),
  credential_source: z.enum(["environment", "awesome"]).nullable().optional(),
  credential_source_available: z.boolean(),
  context_used_tokens: safeIntegerSchema.min(0),
  context_budget_tokens: safeIntegerSchema.min(1),
  changed_file_count: safeIntegerSchema.min(0),
});

export type StatusSnapshot = z.infer<typeof statusSnapshotSchema>;

export const commandOwners: Readonly<Record<CommandName, CommandOwner>> = {
  new: "application",
  rename: "application",
  resume: "application",
  context: "application",
  compact: "application",
  model: "application",
  auth: "application",
  thinking: "application",
  workspace: "application",
  diff: "application",
  undo: "application",
  redo: "application",
  tools: "application",
  skills: "application",
  mcp: "application",
  memory: "application",
  status: "application",
  usage: "application",
  doctor: "application",
  config: "application",
  permissions: "application",
  help: "ink",
  theme: "ink",
  copy: "ink",
  quit: "ink",
};

export const commandIntentSchema = z.strictObject({
  name: commandNameSchema,
  arguments: z.array(z.string()).optional(),
});

export const commandOptionSchema = z.strictObject({
  value: boundedText(1, 200),
  label: boundedText(1, 200),
  description: boundedText(0, 1_000).optional(),
  selected: z.boolean(),
  disabled: z.boolean(),
});

export const commandSelectionSchema = z
  .strictObject({
    kind: z.literal("selection"),
    prompt: boundedText(1, 1_000),
    options: z.array(commandOptionSchema).min(1),
  })
  .superRefine(({ options }, context) => {
    const values = new Set(options.map((option) => option.value));
    if (values.size !== options.length) {
      context.addIssue({
        code: "custom",
        message: "Command option values must be unique",
      });
    }
    if (options.filter((option) => option.selected).length > 1) {
      context.addIssue({
        code: "custom",
        message: "At most one option may be selected",
      });
    }
  });

export const commandSecretPromptSchema = z.strictObject({
  kind: z.literal("secret"),
  provider: z.enum(["deepseek", "kimi", "mem0"]),
  action: z.enum(["add", "replace"]),
  label: boundedText(1, 200),
  environment_variable: boundedText(1, 128),
  help_url: boundedText(1, 2_000),
});

export const commandApplicationInteractionSchema = z.strictObject({
  kind: z.literal("application"),
  interaction_id: boundedText(1, 128),
});

const contextCategorySchema = z.strictObject({
  name: z.enum(["instructions", "conversation", "files", "memory"]),
  estimated_tokens: safeIntegerSchema.min(0),
});
const toolItemSchema = z.strictObject({
  name: boundedText(1, 128),
  description: boundedText(1, 1_000),
  read_only: z.boolean(),
  approval_required: z.boolean(),
});
const skillItemSchema = z.strictObject({
  name: boundedText(1, 64),
  description: boundedText(1, 500),
  source: z.enum(["bundled", "user", "workspace"]),
});
const skillDiagnosticSchema = z.strictObject({
  code: boundedText(1, 128),
  name: boundedText(1, 64).nullable().optional(),
  source: z.enum(["bundled", "user", "workspace"]),
  message: boundedText(1, 1_000),
});
const mcpItemSchema = z.strictObject({
  server_id: boundedText(1, 128),
  state: z.enum([
    "disabled",
    "untrusted",
    "enablement_required",
    "configured",
    "connected",
    "error",
  ]),
  detail: boundedText(1, 2_000).nullable().optional(),
});
const memoryEntrySchema = z.strictObject({
  id: boundedText(1, 128),
  content: boundedText(1, 2_000),
});
const memorySearchItemSchema = z.strictObject({
  id: boundedText(1, 128),
  content: boundedText(1, 500),
  scope: z.enum(["user", "workspace"]),
  fact_hash: z.string().regex(/^[a-f0-9]{64}$/u),
});
const doctorCheckSchema = z.strictObject({
  name: boundedText(1, 128),
  status: z.enum([
    "ok",
    "missing",
    "valid",
    "invalid",
    "unverified",
    "off",
    "error",
  ]),
  detail: boundedText(1, 2_000).nullable().optional(),
});

export const threadTransitionSnapshotSchema = z
  .strictObject({
    reason: z.enum(["new", "resume"]),
    application: applicationStateSchema,
    thread: threadReadResultSchema,
  })
  .superRefine(({ application, thread }, context) => {
    if (application.current_thread_id !== thread.view.thread.id) {
      context.addIssue({
        code: "custom",
        message: "Thread transition identities must match",
      });
    }
  });

export const commandPayloadSchema = z.discriminatedUnion("kind", [
  z.strictObject({
    kind: z.literal("notice"),
    message: boundedText(1, 30_000),
  }),
  z.strictObject({
    kind: z.literal("thread_transition"),
    transition: threadTransitionSnapshotSchema,
  }),
  z.strictObject({
    kind: z.literal("thread_renamed"),
    thread: threadSchema,
  }),
  z.strictObject({
    kind: z.literal("context"),
    categories: z.array(contextCategorySchema),
    total_tokens: safeIntegerSchema.min(0),
    budget_tokens: safeIntegerSchema.min(1),
  }),
  z.strictObject({
    kind: z.literal("compact"),
    old_covered_entry_sequence: safeIntegerSchema.min(0),
    new_covered_entry_sequence: safeIntegerSchema.min(0),
    usage: usageSummarySchema,
  }),
  z.strictObject({
    kind: z.literal("model"),
    model: boundedText(1, 200),
    default_model_updated: z.boolean(),
  }),
  z.strictObject({ kind: z.literal("thinking"), enabled: z.boolean() }),
  z.strictObject({ kind: z.literal("workspace"), path: boundedText(1, 4_096) }),
  z.strictObject({
    kind: z.literal("diff"),
    change_set_id: boundedText(1, 128).nullable().optional(),
    content: boundedText(0, 100_000),
  }),
  z.strictObject({
    kind: z.literal("change"),
    action: z.enum(["undo", "redo"]),
    change_set_id: boundedText(1, 128),
    lifecycle: boundedText(1, 64),
    restored_paths: z.array(z.string()).max(1_000),
    warning: boundedText(1, 2_000).nullable().optional(),
  }),
  z.strictObject({
    kind: z.literal("tools"),
    permission_mode: z.enum(["request_approval", "full_access"]),
    tools: z.array(toolItemSchema),
  }),
  z.strictObject({
    kind: z.literal("skills"),
    active_mode: boundedText(1, 64),
    skills: z.array(skillItemSchema),
    diagnostics: z.array(skillDiagnosticSchema),
  }),
  z.strictObject({ kind: z.literal("mcp"), servers: z.array(mcpItemSchema) }),
  z.strictObject({
    kind: z.literal("memory_status"),
    local_available: z.boolean(),
    local_enabled: z.boolean(),
    cloud_provider: z.literal("mem0"),
    cloud_available: z.boolean(),
    cloud_enabled: z.boolean(),
    cloud_error_code: boundedText(1, 128).nullable().optional(),
  }),
  z.strictObject({
    kind: z.literal("memory_document"),
    scope: z.enum(["user", "workspace"]),
    content_hash: z.string().regex(/^[a-f0-9]{64}$/u),
    entries: z.array(memoryEntrySchema),
  }),
  z.strictObject({
    kind: z.literal("memory_search"),
    provider: z.literal("mem0"),
    memories: z.array(memorySearchItemSchema),
  }),
  z.strictObject({
    kind: z.literal("memory_mutation"),
    provider: z.enum(["local", "mem0"]),
    status: boundedText(1, 64),
    scope: z.enum(["user", "workspace"]).nullable().optional(),
    entry_id: boundedText(1, 128).nullable().optional(),
    memory_id: boundedText(1, 128).nullable().optional(),
    error_code: boundedText(1, 128).nullable().optional(),
  }),
  z.strictObject({ kind: z.literal("status"), snapshot: statusSnapshotSchema }),
  z.strictObject({ kind: z.literal("usage"), usage: usageSummarySchema }),
  z.strictObject({
    kind: z.literal("doctor"),
    checks: z.array(doctorCheckSchema),
  }),
  z.strictObject({
    kind: z.literal("config"),
    sources: z.array(z.string()),
    credentials: providerCredentialStatusesSchema,
  }),
  z.strictObject({
    kind: z.literal("permissions"),
    mode: z.enum(["request_approval", "full_access"]),
  }),
]);

export const commandInteractionSchema = z.discriminatedUnion("kind", [
  commandSelectionSchema,
  commandSecretPromptSchema,
  commandApplicationInteractionSchema,
]);
export const commandOutcomeSchema = z.discriminatedUnion("kind", [
  z.strictObject({ kind: z.literal("result"), payload: commandPayloadSchema }),
  z.strictObject({
    kind: z.literal("interaction"),
    interaction: commandInteractionSchema,
    context: commandPayloadSchema.nullable().optional(),
  }),
  z.strictObject({
    kind: z.literal("error"),
    code: boundedText(1, 128),
    message: boundedText(1, 30_000),
  }),
]);

export type CommandPayload = z.infer<typeof commandPayloadSchema>;
export type ThreadTransitionSnapshot = Extract<
  CommandPayload,
  { readonly kind: "thread_transition" }
>["transition"];
export type CommandOutcome = z.infer<typeof commandOutcomeSchema>;
export type CommandSelection = z.infer<typeof commandSelectionSchema>;
export type CommandSecretPrompt = z.infer<typeof commandSecretPromptSchema>;
