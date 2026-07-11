import { z } from "zod";

import { boundedText, jsonValueSchema, safeIntegerSchema } from "./base.js";

export const applicationCommandNames = [
  "new",
  "resume",
  "context",
  "compact",
  "model",
  "auth",
  "thinking",
  "workspace",
  "diff",
  "undo",
  "redo",
  "tools",
  "skills",
  "skill",
  "mcp",
  "memory",
  "status",
  "usage",
  "doctor",
  "config",
] as const;

export const skillCommandNames = [
  "init",
  "review",
  "debug",
  "test",
  "commit",
] as const;

export const inkCommandNames = ["help", "theme", "copy", "quit"] as const;

export const commandNames = [
  ...applicationCommandNames,
  ...skillCommandNames,
  ...inkCommandNames,
] as const;

export const commandNameSchema = z.enum(commandNames);
export const commandOwnerSchema = z.enum(["application", "skill", "ink"]);

export type CommandName = z.infer<typeof commandNameSchema>;
export type CommandOwner = z.infer<typeof commandOwnerSchema>;

export const statusSnapshotSchema = z.strictObject({
  version: z.string().regex(/^\d+\.\d+\.\d+$/u),
  workspace_path: boundedText(1, 4_096),
  thread_title: boundedText(1, 500),
  thread_id: boundedText(1, 128),
  thread_display_id: boundedText(1, 128),
  model_id: boundedText(1, 200),
  model_status: z.enum(["configured", "not_configured"]),
  thinking_enabled: z.boolean(),
  skill_mode: boundedText(1, 64),
  local_memory_enabled: z.boolean(),
  mem0_enabled: z.boolean(),
  mcp_ready: safeIntegerSchema.min(0),
  mcp_degraded: safeIntegerSchema.min(0),
  operation_status: z.enum(["idle", "active"]),
  operation_id: boundedText(1, 128).nullable(),
  configuration_valid: z.boolean(),
  configuration_diagnostic_count: safeIntegerSchema.min(0),
});

export type StatusSnapshot = z.infer<typeof statusSnapshotSchema>;

export const commandOwners: Readonly<Record<CommandName, CommandOwner>> = {
  new: "application",
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
  skill: "application",
  mcp: "application",
  memory: "application",
  status: "application",
  usage: "application",
  doctor: "application",
  config: "application",
  init: "skill",
  review: "skill",
  debug: "skill",
  test: "skill",
  commit: "skill",
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
});

export const commandSelectionSchema = z
  .strictObject({
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
  provider: z.enum(["deepseek", "kimi"]),
  action: z.enum(["add", "replace"]),
  label: boundedText(1, 200),
  environment_variable: boundedText(1, 128),
  help_url: boundedText(1, 2_000),
});

export const commandResultSchema = z
  .strictObject({
    status: z.enum(["success", "error", "interaction_required"]),
    content: boundedText(0, 30_000),
    data: z.record(z.string(), jsonValueSchema),
    selection: commandSelectionSchema.optional(),
    secret_prompt: commandSecretPromptSchema.optional(),
  })
  .superRefine(({ selection, secret_prompt }, context) => {
    if (selection && secret_prompt) {
      context.addIssue({
        code: "custom",
        message: "Command result cannot request two inputs",
      });
    }
  });
