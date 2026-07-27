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
  threadEntrySchema,
  threadTransitionSnapshotSchema,
} from "../../src/protocol/index.js";
import { z } from "zod";
import { loadFixtureCorpus } from "./fixture-loader.js";

describe("protocol inventory", () => {
  it("contains the complete product method inventory", () => {
    expect(methodNames).toEqual([
      "initialize",
      "skill.list",
      "skill.install",
      "skill.remove",
      "application.getState",
      "thread.list",
      "thread.search",
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

  it("validates one authoritative Application and Thread transition", async () => {
    const corpus = await loadFixtureCorpus();
    const fixtures = corpus.files["command-results.valid.json"] as {
      cases: { outcome: unknown }[];
    };
    const result = fixtures.cases
      .map((fixture) => fixture.outcome)
      .find(
        (candidate) =>
          (candidate as { payload?: { kind?: string } }).payload?.kind ===
          "thread_transition",
      ) as {
      payload: {
        transition: {
          application: { current_thread_id: string };
          thread: { view: { thread: { id: string } } };
        };
      };
    };
    expect(
      threadTransitionSnapshotSchema.safeParse(result.payload.transition)
        .success,
    ).toBe(true);

    const mismatched = structuredClone(result.payload.transition);
    mismatched.thread.view.thread.id = "thread_mismatch";
    expect(threadTransitionSnapshotSchema.safeParse(mismatched).success).toBe(
      false,
    );
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
      "memory.status",
      "interaction.required",
      "interaction.resolved",
      "warning",
    ]);
  });

  it("freezes command ownership and excludes removed commands", () => {
    expect(applicationCommandNames).toHaveLength(26);
    expect(applicationCommandNames).toContain("fork");
    expect(applicationCommandNames).toContain("retry");
    expect(applicationCommandNames).toContain("auth");
    expect(applicationCommandNames).toContain("permissions");
    expect(applicationCommandNames).toContain("web");
    expect(applicationCommandNames).toContain("search");
    expect(applicationCommandNames).toContain("export");
    expect(inkCommandNames).toEqual(["help", "theme", "copy", "quit"]);
    expect(commandNameSchema.safeParse("init").success).toBe(false);
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
    expect(requestIdSchema.safeParse("\ud800").success).toBe(false);
    expect(requestIdSchema.safeParse("\udc00").success).toBe(false);
    expect(requestIdSchema.safeParse("😀").success).toBe(true);
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

  it("matches the direct execute transport and tool command limit", () => {
    const schema = methodSchemas["direct.execute"].params;
    expect(
      schema.safeParse({ thread_id: "thread_1", command: "x".repeat(8_000) })
        .success,
    ).toBe(true);
    expect(
      schema.safeParse({ thread_id: "thread_1", command: "x".repeat(8_001) })
        .success,
    ).toBe(false);
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
