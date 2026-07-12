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
  boundedText(1, 128),
  safeIntegerSchema,
]);

export const productErrorCodes = [
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
  "checkpoint_missing",
  "checkpoint_corrupt",
  "recovery_required",
  "client_version_incompatible",
  "protocol_version_incompatible",
  "internal_error",
] as const;

export const productErrorCodeSchema = z.enum(productErrorCodes);

export const productErrorSchema = z.strictObject({
  code: productErrorCodeSchema,
  message: boundedText(1, 2_000),
  retryable: z.boolean(),
  data: z.record(z.string(), jsonValueSchema),
});

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
