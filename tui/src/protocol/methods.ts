import { z } from "zod";

import {
  applicationResultSchema,
  boundedText,
  interactionDecisionSchema,
  productErrorSchema,
  safeIntegerSchema,
} from "./base.js";
import { commandIntentSchema, commandOutcomeSchema } from "./commands.js";
import {
  applicationStateSchema,
  credentialSourceSchema,
  threadReadResultSchema,
  threadSchema,
  workspacePresentationSchema,
} from "./product-projections.js";

const positiveIntegerSchema = safeIntegerSchema.min(1);
const emptyParamsSchema = z.strictObject({});
const identifierSchema = boundedText(1, 128);
const clientMessageIdentifierSchema = identifierSchema.regex(
  /^client_[A-Za-z0-9_-]+$/,
);

export const initializeParamsSchema = z.strictObject({
  protocol_version: z.literal(3),
  client_name: z.literal("awesome"),
  client_version: boundedText(1, 64),
});

export const initializeResultSchema = z.strictObject({
  product_version: boundedText(1, 64),
  protocol_version: z.literal(3),
  status: z.enum(["ready", "trust_required", "state_reset_required"]),
  session_id: identifierSchema,
  interaction_id: identifierSchema.optional(),
  workspace: workspacePresentationSchema,
  capabilities: z.array(z.string()),
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
const operationAcceptedSchema = z.strictObject({
  operation_id: identifierSchema,
  thread_id: identifierSchema.optional(),
  turn_id: identifierSchema.optional(),
  client_message_id: clientMessageIdentifierSchema.optional(),
});
const turnOperationAcceptedSchema = operationAcceptedSchema.extend({
  thread_id: identifierSchema,
  turn_id: identifierSchema,
  client_message_id: clientMessageIdentifierSchema,
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
  provider: z.enum(["deepseek", "kimi", "mem0"]),
  status: z.enum(["configured", "deleted", "invalid", "confirm_unverified"]),
  source: credentialSourceSchema.optional(),
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
      client_message_id: clientMessageIdentifierSchema,
    }),
    value: turnOperationAcceptedSchema,
    result: applicationResultSchema(turnOperationAcceptedSchema),
  },
  "direct.execute": {
    params: z.strictObject({
      thread_id: identifierSchema,
      command: boundedText(1, 8_000),
    }),
    value: operationAcceptedSchema,
    result: applicationResultSchema(operationAcceptedSchema),
  },
  "command.execute": {
    params: commandIntentSchema,
    value: commandOutcomeSchema,
    result: applicationResultSchema(commandOutcomeSchema),
  },
  "provider.credential.set": {
    params: z
      .strictObject({
        provider: z.enum(["deepseek", "kimi", "mem0"]),
        action: z.enum(["add", "replace", "delete"]),
        api_key: boundedText(1, 20_000).optional(),
        allow_unverified: z.boolean().optional(),
      })
      .superRefine(({ action, api_key, allow_unverified }, context) => {
        if (action === "delete" && (api_key || allow_unverified)) {
          context.addIssue({
            code: "custom",
            message: "Delete does not accept credential content",
          });
        } else if (action !== "delete" && !api_key) {
          context.addIssue({
            code: "custom",
            message: "Credential content is required",
          });
        }
      }),
    value: providerCredentialSetResultSchema,
    result: applicationResultSchema(providerCredentialSetResultSchema),
  },
  "interaction.respond": {
    params: z.strictObject({
      interaction_id: identifierSchema,
      decision: interactionDecisionSchema,
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
