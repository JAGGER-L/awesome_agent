import { describe, expect, it } from "vitest";

import type { EventEnvelope } from "../../src/protocol/index.js";
import {
  initialSurfaceState,
  surfaceReducer,
} from "../../src/state/reducer.js";

function lifecycle(
  sequence: number,
  kind: EventEnvelope["event_type"],
): EventEnvelope {
  const payload = kind.startsWith("operation.")
    ? { kind, message: "" }
    : kind.startsWith("turn.")
      ? { kind }
      : kind === "warning"
        ? { kind, code: "safe", message: "warning" }
        : {
            kind: "usage.updated",
            input_tokens: 1,
            output_tokens: 2,
            reasoning_tokens: 0,
            cache_read_tokens: 0,
            cache_write_tokens: 0,
          };
  return {
    version: 1,
    event_id: `event_${sequence}`,
    sequence,
    session_id: "session_1",
    workspace_key: "workspace_1",
    thread_id: "thread_1",
    turn_id: "turn_1",
    operation_id: "operation_1",
    event_type: kind,
    timestamp: "2026-07-11T08:00:00Z",
    payload,
  } as EventEnvelope;
}

describe("surfaceReducer", () => {
  it("moves an optimistic user message through pending, accepted, and failed", () => {
    let state = surfaceReducer(initialSurfaceState(), {
      type: "transcript.user.pending",
      generation: 0,
      client_message_id: "client_1",
      text: "inspect",
    });
    expect(state.committed_transcript).toEqual([
      expect.objectContaining({ status: "pending", text: "inspect" }),
    ]);

    state = surfaceReducer(state, {
      type: "transcript.user.accepted",
      generation: 0,
      client_message_id: "client_1",
    });
    expect(state.committed_transcript).toEqual([
      expect.objectContaining({ status: "accepted" }),
    ]);

    state = surfaceReducer(state, {
      type: "transcript.user.failed",
      generation: 0,
      client_message_id: "client_1",
      message: "busy",
    });
    expect(state.committed_transcript).toEqual([
      expect.objectContaining({ status: "failed", error_message: "busy" }),
    ]);
  });

  it("atomically replaces every thread-scoped projection and increments generation", () => {
    const dirty = {
      ...initialSurfaceState(),
      connection: "ready" as const,
      thread_generation: 4,
      application: { current_thread_id: "thread_old" } as never,
      thread: { view: { thread: { id: "thread_old" } } } as never,
      active_operation: { id: "operation_old", status: "active" as const },
      usage: { input_tokens: 10 },
      latest_change: {
        change_set_id: "change_old",
        paths: ["old.py"],
        reversibility: "full" as const,
      },
      pending_interaction: {
        interaction_id: "interaction_old",
        interaction_kind: "tool_approval" as const,
        prompt: "old",
        operation: "edit",
        target: "old.py",
        choices: [{ decision: "deny", label: "No" }],
      },
      warnings: [{ code: "old", message: "old" }],
      committed_transcript: [
        { key: "old", kind: "status" as const, message: "old" },
      ],
      transcript_persisted: false,
    };
    const application = { current_thread_id: "thread_new" } as never;
    const thread = { view: { thread: { id: "thread_new" } } } as never;

    const replaced = surfaceReducer(dirty, {
      type: "thread.replaced",
      application,
      thread,
      transcript: [],
    });

    expect(replaced).toEqual({
      connection: "ready",
      event_sequence: dirty.event_sequence,
      thread_generation: 5,
      application,
      thread,
      warnings: [],
      committed_transcript: [],
      transcript_persisted: true,
    });
  });

  it("ignores reconciliation captured by an older thread generation", () => {
    const state = { ...initialSurfaceState(), thread_generation: 2 };
    const result = surfaceReducer(state, {
      type: "transcript.reconciled",
      generation: 1,
      result: {
        persisted: true,
        blocks: [{ key: "old", kind: "status", message: "old" }],
      },
    });

    expect(result).toBe(state);
  });

  it("ignores command feedback captured by an older thread generation", () => {
    const state = { ...initialSurfaceState(), thread_generation: 2 };
    const result = surfaceReducer(state, {
      type: "transcript.command_result",
      generation: 1,
      block: {
        key: "old-command",
        kind: "command_result",
        command: "usage",
        tone: "info",
        content: "old command result",
      },
    });

    expect(result).toBe(state);
  });

  it("advances the stream cursor without projecting stale thread events", () => {
    const state = {
      ...initialSurfaceState(),
      thread_generation: 2,
      event_sequence: 7,
    };
    const result = surfaceReducer(state, {
      type: "event.received",
      generation: 1,
      event: lifecycle(8, "warning"),
    });

    expect(result.event_sequence).toBe(8);
    expect(result.warnings).toEqual([]);
  });

  it("moves through connection and handshake states", () => {
    let state = initialSurfaceState();
    state = surfaceReducer(state, { type: "connection.start" });
    state = surfaceReducer(state, { type: "connection.handshaking" });
    state = surfaceReducer(state, { type: "handshake.ready" });
    expect(state.connection).toBe("ready");
  });

  it("projects one active Operation and Turn with coalesced deltas", () => {
    let state = initialSurfaceState();
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: lifecycle(1, "operation.started"),
    });
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: lifecycle(2, "turn.started"),
    });
    state = surfaceReducer(state, {
      type: "delta.received",
      generation: 0,
      delta: {
        kind: "coalesced_delta",
        session_id: "session_1",
        thread_id: "thread_1",
        turn_id: "turn_1",
        operation_id: "operation_1",
        delta_kind: "text",
        text: "hello",
        first_sequence: 3,
        last_sequence: 4,
      },
    });
    expect(state.active_operation?.turn?.assistant_text).toBe("hello");
    expect(state.event_sequence).toBe(4);
  });

  it("enters fatal state for terminal-before-start and duplicate terminals", () => {
    const invalid = surfaceReducer(initialSurfaceState(), {
      type: "event.received",
      generation: 0,
      event: lifecycle(1, "operation.completed"),
    });
    expect(invalid.connection).toBe("fatal");

    let state = surfaceReducer(initialSurfaceState(), {
      type: "event.received",
      generation: 0,
      event: lifecycle(1, "operation.started"),
    });
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: lifecycle(2, "operation.completed"),
    });
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: lifecycle(3, "operation.completed"),
    });
    expect(state.connection).toBe("fatal");
  });

  it("bounds live reasoning and discards it at Turn completion", () => {
    let state = surfaceReducer(initialSurfaceState(), {
      type: "event.received",
      generation: 0,
      event: lifecycle(1, "operation.started"),
    });
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: lifecycle(2, "turn.started"),
    });
    state = surfaceReducer(state, {
      type: "delta.received",
      generation: 0,
      delta: {
        kind: "coalesced_delta",
        session_id: "session_1",
        thread_id: "thread_1",
        turn_id: "turn_1",
        operation_id: "operation_1",
        delta_kind: "reasoning",
        text: "r".repeat(40_000),
        first_sequence: 3,
        last_sequence: 3,
      },
    });
    expect(
      state.active_operation?.turn?.reasoning_text.length,
    ).toBeLessThanOrEqual(32_000);
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: lifecycle(4, "turn.completed"),
    });
    expect(state.active_operation?.turn).toMatchObject({
      reasoning_text: "",
      reasoning_marker: "Thought for 0 ms",
    });
  });

  it("deduplicates warnings and preserves live projection during hydration", () => {
    let state = surfaceReducer(initialSurfaceState(), {
      type: "event.received",
      generation: 0,
      event: lifecycle(1, "operation.started"),
    });
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: lifecycle(2, "warning"),
    });
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: lifecycle(3, "warning"),
    });
    state = surfaceReducer(state, {
      type: "hydrate.application",
      application: { initialized: true } as never,
    });
    expect(state.warnings).toHaveLength(1);
    expect(state.active_operation?.id).toBe("operation_1");
  });

  it("resets reconnect projection and closes", () => {
    let state = surfaceReducer(initialSurfaceState(), {
      type: "connection.start",
    });
    state = surfaceReducer(state, { type: "reconnect.reset" });
    expect(state).toEqual(initialSurfaceState());
    state = surfaceReducer(state, { type: "surface.closed" });
    expect(state.connection).toBe("closed");
  });
});
