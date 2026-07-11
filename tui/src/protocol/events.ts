import { z } from "zod";

import { boundedText, safeIntegerSchema, utcTimestampSchema } from "./base.js";

export const eventTypes = [
  "operation.started",
  "operation.completed",
  "operation.failed",
  "operation.cancelled",
  "turn.started",
  "turn.completed",
  "turn.failed",
  "turn.cancelled",
  "assistant.text.delta",
  "assistant.reasoning.delta",
  "provider.retrying",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "tool.cancelled",
  "context.prepared",
  "context.compressed",
  "usage.updated",
  "workspace.changed",
  "memory.status",
  "interaction.required",
  "interaction.resolved",
  "warning",
] as const;

export const eventTypeSchema = z.enum(eventTypes);
export type EventType = z.infer<typeof eventTypeSchema>;

const boundedInteger = safeIntegerSchema.min(0);
const nullableIdentifier = boundedText(1, 128)
  .nullish()
  .transform((value) => value ?? undefined);
function lifecyclePayload<
  Kind extends
    | "operation.started"
    | "operation.completed"
    | "operation.failed"
    | "operation.cancelled",
>(kind: Kind) {
  return z.strictObject({
    kind: z.literal(kind),
    message: boundedText(0, 2_000),
  });
}

function turnPayload<
  Kind extends
    | "turn.started"
    | "turn.completed"
    | "turn.failed"
    | "turn.cancelled",
>(kind: Kind) {
  return z.strictObject({
    kind: z.literal(kind),
    reason: boundedText(0, 200).optional(),
  });
}

function toolResultPayload<
  Kind extends "tool.completed" | "tool.failed" | "tool.cancelled",
>(kind: Kind) {
  return z.strictObject({
    kind: z.literal(kind),
    call_id: boundedText(1, 128),
    tool_name: boundedText(1, 200),
    summary: boundedText(0, 2_000),
    error_code: boundedText(0, 128).optional(),
  });
}

function contextPayload<Kind extends "context.prepared" | "context.compressed">(
  kind: Kind,
) {
  return z.strictObject({
    kind: z.literal(kind),
    source_count: boundedInteger.max(10_000),
    estimated_tokens: boundedInteger,
  });
}

export const eventPayloadSchema = z.discriminatedUnion("kind", [
  lifecyclePayload("operation.started"),
  lifecyclePayload("operation.completed"),
  lifecyclePayload("operation.failed"),
  lifecyclePayload("operation.cancelled"),
  turnPayload("turn.started"),
  turnPayload("turn.completed"),
  turnPayload("turn.failed"),
  turnPayload("turn.cancelled"),
  z.strictObject({
    kind: z.literal("assistant.text.delta"),
    text: boundedText(1, 30_000),
  }),
  z.strictObject({
    kind: z.literal("assistant.reasoning.delta"),
    text: boundedText(1, 30_000),
  }),
  z.strictObject({
    kind: z.literal("provider.retrying"),
    attempt: safeIntegerSchema.min(2).max(7),
    maximum: safeIntegerSchema.min(1).max(7),
    delay_seconds: z.number().finite().min(0).max(30),
    error_code: boundedText(1, 128),
  }),
  z.strictObject({
    kind: z.literal("tool.started"),
    call_id: boundedText(1, 128),
    tool_name: boundedText(1, 200),
  }),
  toolResultPayload("tool.completed"),
  toolResultPayload("tool.failed"),
  toolResultPayload("tool.cancelled"),
  contextPayload("context.prepared"),
  contextPayload("context.compressed"),
  z.strictObject({
    kind: z.literal("usage.updated"),
    input_tokens: boundedInteger,
    output_tokens: boundedInteger,
    reasoning_tokens: boundedInteger,
    cache_read_tokens: boundedInteger,
    cache_write_tokens: boundedInteger,
  }),
  z.strictObject({
    kind: z.literal("workspace.changed"),
    change_set_id: boundedText(1, 128),
    paths: z.array(z.string()).max(1_000),
    reversibility: z.enum(["full", "partial", "none"]),
  }),
  z.strictObject({
    kind: z.literal("memory.status"),
    layer: z.enum(["local", "external"]),
    enabled: z.boolean(),
    status: boundedText(1, 128),
  }),
  z.strictObject({
    kind: z.literal("interaction.required"),
    interaction_id: boundedText(1, 128),
    interaction_kind: z.enum([
      "workspace_trust",
      "execute_boundary",
      "recovery_decision",
    ]),
    prompt: boundedText(1, 2_000),
    choices: z.array(z.string()).min(1).max(16),
  }),
  z.strictObject({
    kind: z.literal("interaction.resolved"),
    interaction_id: boundedText(1, 128),
    decision: boundedText(1, 128),
  }),
  z.strictObject({
    kind: z.literal("warning"),
    code: boundedText(1, 128),
    message: boundedText(1, 2_000),
  }),
]);

const operationTypes = new Set<EventType>([
  "operation.started",
  "operation.completed",
  "operation.failed",
  "operation.cancelled",
]);
const turnTypes = new Set<EventType>([
  "turn.started",
  "turn.completed",
  "turn.failed",
  "turn.cancelled",
]);

export const eventEnvelopeSchema = z
  .strictObject({
    version: z.literal(1),
    event_id: z
      .string()
      .max(128)
      .regex(/^event_[A-Za-z0-9]+$/),
    sequence: safeIntegerSchema.min(1),
    session_id: boundedText(1, 128),
    workspace_key: boundedText(1, 512),
    thread_id: nullableIdentifier,
    turn_id: nullableIdentifier,
    operation_id: nullableIdentifier,
    event_type: eventTypeSchema,
    timestamp: utcTimestampSchema,
    payload: eventPayloadSchema,
  })
  .superRefine((event, context) => {
    if (event.event_type !== event.payload.kind) {
      context.addIssue({
        code: "custom",
        message: "event_type must match payload kind",
      });
    }
    if (operationTypes.has(event.event_type) && !event.operation_id) {
      context.addIssue({
        code: "custom",
        message: "Operation event requires operation_id",
      });
    }
    if (
      turnTypes.has(event.event_type) &&
      (!event.thread_id || !event.turn_id)
    ) {
      context.addIssue({
        code: "custom",
        message: "Turn event requires thread_id and turn_id",
      });
    }
  });

export type EventEnvelope = z.infer<typeof eventEnvelopeSchema>;
