import { z } from "zod";

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue };

const jsonNumberSchema = z
  .number()
  .finite()
  .refine((value) => !Number.isInteger(value) || Number.isSafeInteger(value), {
    message: "JSON integers must be within the JavaScript safe integer range",
  });

export const jsonValueSchema: z.ZodType<JsonValue> = z.lazy(() =>
  z.union([
    z.null(),
    z.boolean(),
    jsonNumberSchema,
    z.string(),
    z.array(jsonValueSchema),
    z.record(z.string(), jsonValueSchema),
  ]),
);

export const safeIntegerSchema = z
  .number()
  .int()
  .min(Number.MIN_SAFE_INTEGER)
  .max(Number.MAX_SAFE_INTEGER);

function isWellFormedUnicode(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

export const permissionModeSchema = z.enum([
  "request_approval",
  "accept_edits",
  "full_access",
]);
export type PermissionMode = z.infer<typeof permissionModeSchema>;

export function boundedText(minimum: number, maximum: number) {
  return z.string().superRefine((value, context) => {
    const length = Array.from(value).length;
    if (length < minimum || length > maximum) {
      context.addIssue({
        code: "custom",
        message: `Expected between ${minimum} and ${maximum} Unicode code points`,
      });
    }
  });
}

export const utcTimestampSchema = boundedText(1, 64).refine(
  (value) => /(?:Z|\+00:00)$/.test(value) && Number.isFinite(Date.parse(value)),
  "Expected a valid UTC timestamp",
);

export const requestIdSchema = z.union([
  boundedText(1, 128).refine(
    isWellFormedUnicode,
    "Expected well-formed Unicode without unpaired surrogates",
  ),
  safeIntegerSchema,
]);

export const interactionDecisionSchema = z.enum([
  "trust",
  "reset_state",
  "allow_once",
  "allow_thread_writes",
  "allow_thread_network",
  "enable_full_access",
  "retry",
  "abort",
  "deny",
]);

const genericProductErrorCodes = [
  "configuration_invalid",
  "workspace_not_trusted",
  "thread_not_found",
  "turn_not_found",
  "turn_busy",
  "operation_busy",
  "model_not_configured",
  "provider_not_configured",
  "invalid_arguments",
  "command_not_available",
  "result_too_large",
  "checkpoint_missing",
  "checkpoint_corrupt",
  "recovery_required",
  "client_version_incompatible",
  "protocol_version_incompatible",
  "internal_error",
] as const;

const storageProductErrorCodes = [
  "state_created_by_newer_version",
  "state_unknown",
  "state_unavailable",
  "state_reset_busy",
  "state_reset_failed",
] as const;

export const productErrorCodes = [
  ...genericProductErrorCodes,
  ...storageProductErrorCodes,
] as const;

export const productErrorCodeSchema = z.enum(productErrorCodes);

const stateDirectoryDataSchema = z.strictObject({
  state_directory: boundedText(1, 4_096),
});

const newerStateDataSchema = z.strictObject({
  found_schema: safeIntegerSchema,
  expected_schema: safeIntegerSchema,
  state_directory: boundedText(1, 4_096),
});

const stateResetFailedDataSchema = z.strictObject({
  diagnostic_code: boundedText(1, 128),
  state_directory: boundedText(1, 4_096),
});

const genericProductErrorSchema = z.strictObject({
  code: z.enum(genericProductErrorCodes),
  message: boundedText(1, 2_000),
  retryable: z.boolean(),
  data: z.record(z.string(), jsonValueSchema),
});

const newerStateErrorSchema = z.strictObject({
  code: z.literal("state_created_by_newer_version"),
  message: boundedText(1, 2_000),
  retryable: z.literal(false),
  data: newerStateDataSchema,
});

const stateUnknownErrorSchema = z.strictObject({
  code: z.literal("state_unknown"),
  message: boundedText(1, 2_000),
  retryable: z.literal(false),
  data: stateDirectoryDataSchema,
});

const stateUnavailableErrorSchema = z.strictObject({
  code: z.literal("state_unavailable"),
  message: boundedText(1, 2_000),
  retryable: z.literal(true),
  data: stateDirectoryDataSchema,
});

const stateResetBusyErrorSchema = z.strictObject({
  code: z.literal("state_reset_busy"),
  message: boundedText(1, 2_000),
  retryable: z.literal(true),
  data: stateDirectoryDataSchema,
});

const stateResetFailedErrorSchema = z.strictObject({
  code: z.literal("state_reset_failed"),
  message: boundedText(1, 2_000),
  retryable: z.literal(true),
  data: stateResetFailedDataSchema,
});

export const productErrorSchema = z.discriminatedUnion("code", [
  genericProductErrorSchema,
  newerStateErrorSchema,
  stateUnknownErrorSchema,
  stateUnavailableErrorSchema,
  stateResetBusyErrorSchema,
  stateResetFailedErrorSchema,
]);

export type ProductError = z.infer<typeof productErrorSchema>;

export function applicationResultSchema<T extends z.ZodType>(valueSchema: T) {
  return z.discriminatedUnion("ok", [
    z.strictObject({
      ok: z.literal(true),
      value: valueSchema,
    }),
    z.strictObject({
      ok: z.literal(false),
      error: productErrorSchema,
    }),
  ]);
}

export type ApplicationResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: ProductError };
