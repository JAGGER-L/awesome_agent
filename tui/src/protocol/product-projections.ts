import { z } from "zod";

import {
  boundedText,
  jsonValueSchema,
  permissionModeSchema,
  safeIntegerSchema,
  utcTimestampSchema,
} from "./base.js";
import { modelIdentitySchema } from "./identity.js";
import {
  modelCatalogSchema,
  type ProviderDescriptor,
} from "./model-catalog.js";

const nonNegativeIntegerSchema = safeIntegerSchema.min(0);
const positiveIntegerSchema = safeIntegerSchema.min(1);
const identifierSchema = boundedText(1, 128);
const clientMessageIdentifierSchema = identifierSchema.regex(
  /^client_[A-Za-z0-9_-]+$/,
);

export const citationSchema = z.strictObject({
  id: z.string().regex(/^S[1-9][0-9]{0,5}$/u),
  title: boundedText(1, 500).refine(
    (value) =>
      value.trim().length > 0 &&
      !Array.from(value).some((character) => {
        const codePoint = character.codePointAt(0) ?? 0;
        return (
          codePoint < 32 ||
          (codePoint >= 127 && codePoint <= 159) ||
          character === "\u2028" ||
          character === "\u2029"
        );
      }),
    "Expected a non-blank single-line title",
  ),
  url: boundedText(1, 8_000).refine((value) => {
    if (
      value.includes("\\") ||
      Array.from(value).some((character) => {
        const codePoint = character.codePointAt(0) ?? 0;
        return (
          codePoint < 32 ||
          (codePoint >= 127 && codePoint <= 159) ||
          /\s/u.test(character)
        );
      })
    ) {
      return false;
    }
    try {
      const parsed = new URL(value);
      return (
        parsed.protocol === "https:" &&
        parsed.hostname.length > 0 &&
        parsed.username.length === 0 &&
        parsed.password.length === 0
      );
    } catch {
      return false;
    }
  }, "Expected an absolute HTTPS URL"),
});
export type Citation = z.infer<typeof citationSchema>;

const assistantMetadataSchema = z
  .strictObject({
    citations: z.array(citationSchema).max(128),
  })
  .superRefine(({ citations }, context) => {
    if (citations.some((citation, index) => citation.id !== `S${index + 1}`)) {
      context.addIssue({
        code: "custom",
        message: "Citation identifiers must be contiguous",
      });
    }
    if (
      new Set(citations.map((citation) => citation.url)).size !==
      citations.length
    ) {
      context.addIssue({
        code: "custom",
        message: "Citation URLs must be unique",
      });
    }
  });

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
  web_requests: safeIntegerSchema.min(0),
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

export const workspaceInstructionDiagnosticSchema = z.strictObject({
  code: z.enum([
    "workspace_instructions_unsafe_path",
    "workspace_instructions_too_large",
    "workspace_instructions_token_limit",
    "workspace_instructions_binary",
    "workspace_instructions_not_utf8",
    "workspace_instructions_changed",
    "workspace_instructions_unreadable",
  ]),
  source_id: z.literal("AGENTS.md"),
  message: boundedText(1, 500),
});
export type WorkspaceInstructionDiagnostic = z.infer<
  typeof workspaceInstructionDiagnosticSchema
>;

const secretStatusSchema = z.strictObject({
  deepseek_api_key: z.boolean(),
  moonshot_api_key: z.boolean(),
  mem0_api_key: z.boolean(),
});

export const applicationStateSchema = z
  .strictObject({
    initialized: z.boolean(),
    session_id: identifierSchema,
    workspace_key: identifierSchema,
    workspace: workspacePresentationSchema,
    workspace_trusted: z.boolean(),
    current_thread_id: identifierSchema.optional(),
    model_catalog: modelCatalogSchema,
    model_identity: modelIdentitySchema.optional(),
    thinking_enabled: z.boolean(),
    skill_mode: boundedText(1, 64),
    active_operation_id: identifierSchema.optional(),
    pending_interaction_id: identifierSchema.optional(),
    permission_mode: permissionModeSchema,
    workspace_instruction_diagnostic: workspaceInstructionDiagnosticSchema
      .nullable()
      .optional(),
    configuration_valid: z.boolean(),
    secret_status: secretStatusSchema,
    provider_credentials: providerCredentialStatusesSchema,
    memory_status: z.record(z.string(), jsonValueSchema),
    mcp_status: z.array(z.record(z.string(), jsonValueSchema)),
    usage: z.record(z.string(), z.number().finite().min(0)),
    configuration_diagnostics: z.array(z.string()),
  })
  .superRefine((application, context) => {
    const credentialStatuses = Object.values(application.provider_credentials);
    for (const [
      index,
      provider,
    ] of application.model_catalog.providers.entries()) {
      if (
        !credentialStatuses.some(
          (status) => status.provider === provider.credential_id,
        )
      ) {
        context.addIssue({
          code: "custom",
          path: ["model_catalog", "providers", index, "credential_id"],
          message: "Model Provider credential is not published by Application",
        });
      }
    }

    const identity = application.model_identity;
    if (!identity) return;
    const provider = application.model_catalog.providers.find(
      (candidate) => candidate.id === identity.provider,
    );
    if (!provider) {
      context.addIssue({
        code: "custom",
        path: ["model_identity", "provider"],
        message: "Model identity Provider is absent from the catalog",
      });
      return;
    }
    const catalogModels = application.model_catalog.providers.flatMap(
      (candidate) => candidate.models,
    );
    if (
      !catalogModels.some((model) => model.id === identity.configured_model)
    ) {
      context.addIssue({
        code: "custom",
        path: ["model_identity", "configured_model"],
        message: "Configured model is absent from the catalog",
      });
    }
    if (
      !provider.models.some((model) => model.id === identity.effective_model)
    ) {
      context.addIssue({
        code: "custom",
        path: ["model_identity", "effective_model"],
        message: "Effective model does not belong to its catalog Provider",
      });
    }
  });

export type ProviderCredentialStatus = z.infer<
  typeof providerCredentialStatusSchema
>;

export function modelProviderCredentialStatus(
  application: z.infer<typeof applicationStateSchema>,
  provider: ProviderDescriptor,
): ProviderCredentialStatus | undefined {
  return Object.values(application.provider_credentials).find(
    (status) => status.provider === provider.credential_id,
  );
}

export function credentialConfigured(
  status: ProviderCredentialStatus | undefined,
): boolean {
  return status?.selected_source === "environment"
    ? status.environment_configured
    : status?.selected_source === "awesome"
      ? status.awesome_configured
      : false;
}

export const threadLineageSchema = z.strictObject({
  kind: z.enum(["fork", "retry"]),
  source_thread_id: identifierSchema,
  source_turn_id: identifierSchema,
});

export const threadSchema = z.strictObject({
  id: identifierSchema,
  workspace_key: identifierSchema,
  title: boundedText(1, 500),
  title_source: z.enum(["automatic", "manual"]),
  current_model: boundedText(0, 200).optional(),
  thinking_enabled: z.boolean(),
  skill_mode: boundedText(1, 64),
  lineage: threadLineageSchema.nullable(),
  created_at: utcTimestampSchema,
  updated_at: utcTimestampSchema,
});

const threadEntryBase = {
  id: identifierSchema,
  thread_id: identifierSchema,
  sequence: positiveIntegerSchema,
  content: boundedText(0, 200_000),
  created_at: utcTimestampSchema,
} as const;

export const threadEntrySchema = z.discriminatedUnion("kind", [
  z.strictObject({
    ...threadEntryBase,
    kind: z.literal("user_message"),
    client_message_id: clientMessageIdentifierSchema,
    metadata: z.record(z.string(), jsonValueSchema),
  }),
  z.strictObject({
    ...threadEntryBase,
    kind: z.literal("assistant_message"),
    metadata: assistantMetadataSchema,
  }),
  z
    .strictObject({
      ...threadEntryBase,
      kind: z.literal("direct_command"),
      metadata: z.record(z.string(), jsonValueSchema),
    })
    .superRefine(({ content }, context) => {
      if (Array.from(content).length <= 30_000) return;
      context.addIssue({
        code: "custom",
        message: "Direct command exceeds 30000 code points",
      });
    }),
]);

export const budgetSchema = z.strictObject({
  model_calls: positiveIntegerSchema.max(256),
  tool_calls: positiveIntegerSchema.max(512),
  provider_retries: nonNegativeIntegerSchema.max(6),
  compressions: nonNegativeIntegerSchema.max(10),
  active_execution_seconds: positiveIntegerSchema.max(21_600),
  total_context_tokens: positiveIntegerSchema,
  web_requests: nonNegativeIntegerSchema.max(8),
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
