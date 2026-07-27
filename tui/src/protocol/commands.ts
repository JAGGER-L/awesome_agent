import { z } from "zod";

import {
  boundedText,
  permissionModeSchema,
  safeIntegerSchema,
} from "./base.js";
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
  "fork",
  "retry",
  "search",
  "export",
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
  "web",
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
  permission_mode: permissionModeSchema,
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
  fork: "application",
  retry: "application",
  search: "application",
  export: "application",
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
  web: "application",
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
    reason: z.enum(["new", "resume", "fork", "retry"]),
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

const commandPayloadBaseSchema = z.discriminatedUnion("kind", [
  z.strictObject({
    kind: z.literal("notice"),
    message: boundedText(1, 30_000),
  }),
  z.strictObject({
    kind: z.literal("thread_transition"),
    transition: threadTransitionSnapshotSchema,
  }),
  z.strictObject({
    kind: z.literal("thread_retry"),
    transition: threadTransitionSnapshotSchema,
    operation: z.strictObject({
      operation_id: boundedText(1, 128),
      thread_id: boundedText(1, 128),
      turn_id: boundedText(1, 128),
      client_message_id: boundedText(1, 128).regex(/^client_[A-Za-z0-9_-]+$/u),
    }),
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
    kind: z.literal("thread_export"),
    thread_id: boundedText(1, 128),
    path: boundedText(1, 1_000),
    format: z.enum(["markdown", "json"]),
    write_status: z.enum(["created", "updated", "unchanged"]),
    byte_count: safeIntegerSchema.min(0),
    change_set_id: boundedText(1, 128).optional(),
  }),
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
    permission_mode: permissionModeSchema,
    tools: z.array(toolItemSchema),
  }),
  z.strictObject({
    kind: z.literal("web_status"),
    enabled: z.boolean(),
    provider: z.literal("tavily"),
    available: z.boolean(),
    credential_configured: z.boolean(),
    proxy_configured: z.boolean(),
    thread_authorized: z.boolean(),
    requests_per_turn: safeIntegerSchema.min(0).max(8),
    diagnostic_code: boundedText(1, 128)
      .regex(/^[a-z][a-z0-9_]{0,127}$/u)
      .nullable()
      .optional(),
    disclosure: boundedText(1, 2_000),
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
    mode: permissionModeSchema,
  }),
]);

export const commandPayloadSchema = commandPayloadBaseSchema.superRefine(
  (payload, context) => {
    if (payload.kind === "thread_export") {
      const changed = payload.write_status !== "unchanged";
      if (changed !== Boolean(payload.change_set_id)) {
        context.addIssue({
          code: "custom",
          path: ["change_set_id"],
          message: changed
            ? "Changed exports require a change set"
            : "Unchanged exports must not include a change set",
        });
      }
    }
    if (
      payload.kind === "thread_transition" &&
      payload.transition.reason === "retry"
    ) {
      context.addIssue({
        code: "custom",
        path: ["transition", "reason"],
        message: "Retry transitions require a thread_retry payload",
      });
    }
    if (payload.kind === "thread_transition") {
      const { reason, thread } = payload.transition;
      const lineage = thread.view.thread.lineage;
      if (reason === "fork" && lineage?.kind !== "fork") {
        context.addIssue({
          code: "custom",
          path: ["transition", "thread", "view", "thread", "lineage"],
          message: "Fork transitions require fork lineage",
        });
      }
      if (reason === "new" && lineage !== null) {
        context.addIssue({
          code: "custom",
          path: ["transition", "thread", "view", "thread", "lineage"],
          message: "New transitions require null lineage",
        });
      }
    }
    if (payload.kind === "thread_retry") {
      const { transition, operation } = payload;
      if (transition.reason !== "retry") {
        context.addIssue({
          code: "custom",
          path: ["transition", "reason"],
          message: "Thread retry transition reason must be retry",
        });
      }
      if (transition.thread.view.thread.lineage?.kind !== "retry") {
        context.addIssue({
          code: "custom",
          path: ["transition", "thread", "view", "thread", "lineage"],
          message: "Thread retry transition requires retry lineage",
        });
      }
      if (transition.thread.view.thread.id !== operation.thread_id) {
        context.addIssue({
          code: "custom",
          path: ["operation", "thread_id"],
          message:
            "Thread retry transition and operation identities must match",
        });
      }
      const operationTurn = transition.thread.view.turns.find(
        (turn) => turn.id === operation.turn_id,
      );
      if (!operationTurn || operationTurn.thread_id !== operation.thread_id) {
        context.addIssue({
          code: "custom",
          path: ["operation", "turn_id"],
          message: "Thread retry operation Turn must exist in its transition",
        });
        return;
      }
      const inProgressTurns = transition.thread.view.turns.filter(
        (turn) => turn.status === "in_progress",
      );
      if (
        inProgressTurns.length !== 1 ||
        inProgressTurns[0]?.id !== operationTurn.id ||
        transition.thread.view.turns.at(-1)?.id !== operationTurn.id
      ) {
        context.addIssue({
          code: "custom",
          path: ["operation", "turn_id"],
          message:
            "Thread retry operation must identify the final and only in-progress Turn",
        });
      }
      const userEntry = transition.thread.view.entries.find(
        (entry) => entry.id === operationTurn.user_entry_id,
      );
      if (
        userEntry?.kind !== "user_message" ||
        userEntry.client_message_id !== operation.client_message_id
      ) {
        context.addIssue({
          code: "custom",
          path: ["operation", "client_message_id"],
          message:
            "Thread retry operation client identity must match its Turn user Entry",
        });
      }
    }
  },
);

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
export type ThreadTransitionSnapshot = z.infer<
  typeof threadTransitionSnapshotSchema
>;
export type ThreadRetryOperation = Extract<
  CommandPayload,
  { readonly kind: "thread_retry" }
>["operation"];
export type CommandOutcome = z.infer<typeof commandOutcomeSchema>;
export type CommandSelection = z.infer<typeof commandSelectionSchema>;
export type CommandSecretPrompt = z.infer<typeof commandSecretPromptSchema>;
