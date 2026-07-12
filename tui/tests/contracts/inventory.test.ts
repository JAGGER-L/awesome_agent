import { describe, expect, it } from "vitest";

import {
  applicationCommandNames,
  applicationResultSchema,
  commandNameSchema,
  eventEnvelopeSchema,
  eventTypes,
  inkCommandNames,
  jsonValueSchema,
  methodNames,
  methodSchemas,
  requestIdSchema,
  skillCommandNames,
  threadEntrySchema,
} from "../../src/protocol/index.js";
import { z } from "zod";

describe("protocol inventory", () => {
  it("contains the complete product method inventory", () => {
    expect(methodNames).toEqual([
      "initialize",
      "application.getState",
      "thread.list",
      "thread.read",
      "turn.submit",
      "direct.execute",
      "command.execute",
      "provider.credential.set",
      "interaction.respond",
      "operation.cancel",
      "shutdown",
    ]);
  });

  it("contains every merged event discriminator", () => {
    expect(eventTypes).toEqual([
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
    ]);
  });

  it("freezes command ownership and excludes removed commands", () => {
    expect(applicationCommandNames).toHaveLength(20);
    expect(applicationCommandNames).toContain("auth");
    expect(applicationCommandNames).toContain("permissions");
    expect(skillCommandNames).toEqual(["init"]);
    expect(inkCommandNames).toEqual(["help", "theme", "copy", "quit"]);
    expect(commandNameSchema.safeParse("editor").success).toBe(false);
    expect(commandNameSchema.safeParse("details").success).toBe(false);
    for (const removed of [
      "skill",
      "review",
      "debug",
      "test",
      "commit",
      "workplace",
      "clear",
      "exit",
    ]) {
      expect(commandNameSchema.safeParse(removed).success).toBe(false);
    }
  });
});

describe("Python-compatible Unicode limits", () => {
  const entry = (content: string) => ({
    id: "entry_1",
    thread_id: "thread_1",
    sequence: 1,
    kind: "user_message",
    client_message_id: "client_1",
    content,
    metadata: {},
    created_at: "2026-07-11T08:00:00Z",
  });

  it("counts astral code points rather than UTF-16 code units", () => {
    expect(
      threadEntrySchema.safeParse(entry("😀".repeat(200_000))).success,
    ).toBe(true);
    expect(
      threadEntrySchema.safeParse(entry("😀".repeat(200_001))).success,
    ).toBe(false);
  });
});

describe("strict wire boundaries", () => {
  const warning = {
    version: 1,
    event_id: "event_1",
    sequence: 1,
    session_id: "session_1",
    workspace_key: "workspace_1",
    event_type: "warning",
    timestamp: "2026-07-11T08:00:00Z",
    payload: { kind: "warning", code: "safe", message: "Safe warning." },
  };

  it("rejects unsafe and boolean request IDs", () => {
    expect(requestIdSchema.safeParse(true).success).toBe(false);
    expect(requestIdSchema.safeParse(Number.MAX_SAFE_INTEGER + 1).success).toBe(
      false,
    );
    expect(jsonValueSchema.safeParse(Number.MAX_SAFE_INTEGER + 1).success).toBe(
      false,
    );
  });

  it("accepts omitted command arguments like the Python default", () => {
    expect(
      methodSchemas["command.execute"].params.safeParse({ name: "status" })
        .success,
    ).toBe(true);
  });

  it("rejects unknown result branches", () => {
    const schema = applicationResultSchema(z.string());
    expect(
      schema.safeParse({ ok: true, value: "ok", error: null }).success,
    ).toBe(false);
    expect(
      schema.safeParse({ ok: false, error: { code: "internal_error" } })
        .success,
    ).toBe(false);
  });

  it("rejects unknown fields, non-UTC timestamps, and mismatched payloads", () => {
    expect(
      eventEnvelopeSchema.safeParse({ ...warning, private: true }).success,
    ).toBe(false);
    expect(
      eventEnvelopeSchema.safeParse({
        ...warning,
        timestamp: "2026-07-11T16:00:00+08:00",
      }).success,
    ).toBe(false);
    expect(
      eventEnvelopeSchema.safeParse({
        ...warning,
        event_type: "assistant.text.delta",
      }).success,
    ).toBe(false);
  });
});
