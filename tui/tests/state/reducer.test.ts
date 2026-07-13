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
      ? {
          kind,
          ...(kind === "turn.started" ? {} : { duration_ms: 1_000 }),
        }
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
      warnings: [
        { id: "warning:old:1", code: "old", message: "old", count: 1 },
      ],
      committed_transcript: [
        { key: "old", kind: "status" as const, message: "old" },
      ],
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
    });
  });

  it("ignores reconciliation captured by an older thread generation", () => {
    const state = { ...initialSurfaceState(), thread_generation: 2 };
    const result = surfaceReducer(state, {
      type: "transcript.reconciled",
      generation: 1,
      operation_id: "operation_old",
      turn_id: "turn_old",
      blocks: [{ key: "old", kind: "status", message: "old" }],
    });

    expect(result).toBe(state);
  });

  it("hands a terminal operation to the finalized transcript exactly once", () => {
    const blocks = [
      { key: "assistant:1", kind: "assistant" as const, text: "done" },
      { key: "worked:1", kind: "worked" as const, duration_ms: 1_000 },
    ];
    const state = {
      ...initialSurfaceState(),
      committed_transcript: [
        { key: "entry:old", kind: "assistant" as const, text: "old answer" },
      ],
      latest_change: {
        change_set_id: "change_1",
        paths: ["done.py"],
        reversibility: "full" as const,
        operation_id: "operation_1",
      },
      warnings: [
        {
          id: "warning:owned",
          code: "owned",
          message: "owned",
          count: 1,
          operation_id: "operation_1",
        },
        {
          id: "warning:other",
          code: "other",
          message: "other",
          count: 1,
          operation_id: "operation_2",
        },
      ],
      active_operation: {
        id: "operation_1",
        status: "completed" as const,
        turn: {
          id: "turn_1",
          status: "completed" as const,
          started_at: "2026-07-13T00:00:00Z",
          thinking_sequence: 0,
          timeline: [],
        },
      },
    };

    const next = surfaceReducer(state, {
      type: "transcript.reconciled",
      generation: 0,
      operation_id: "operation_1",
      turn_id: "turn_1",
      blocks,
    });

    expect(next.committed_transcript).toEqual([
      { key: "entry:old", kind: "assistant", text: "old answer" },
      ...blocks,
    ]);
    expect(
      next.committed_transcript?.filter(
        (block) => block.kind === "assistant" && block.text === "old answer",
      ),
    ).toHaveLength(1);
    expect(next.active_operation).toBeUndefined();
    expect(next.latest_change).toBeUndefined();
    expect(next.warnings).toEqual([
      expect.objectContaining({ operation_id: "operation_2" }),
    ]);
  });

  it("does not release a newer operation during delayed reconciliation", () => {
    const state = {
      ...initialSurfaceState(),
      active_operation: { id: "operation_2", status: "active" as const },
    };

    const next = surfaceReducer(state, {
      type: "transcript.reconciled",
      generation: 0,
      operation_id: "operation_1",
      turn_id: "turn_1",
      blocks: [],
    });

    expect(next.active_operation).toEqual(state.active_operation);
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
        presentation: {
          kind: "notice",
          message: "old command result",
          tone: "info",
        },
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
        first_timestamp: "2026-07-11T08:00:01Z",
        last_timestamp: "2026-07-11T08:00:01Z",
        first_sequence: 3,
        last_sequence: 4,
      },
    });
    expect(state.active_operation?.turn?.timeline).toEqual([
      expect.objectContaining({ kind: "assistant", text: "hello" }),
    ]);
    expect(state.event_sequence).toBe(4);
  });

  it("assigns stable unique identities to assistant segments separated by tools", () => {
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
    const text = (sequence: number, value: string) => ({
      type: "delta.received" as const,
      generation: 0,
      delta: {
        kind: "coalesced_delta" as const,
        session_id: "session_1",
        thread_id: "thread_1",
        turn_id: "turn_1",
        operation_id: "operation_1",
        delta_kind: "text" as const,
        text: value,
        first_timestamp: "2026-07-11T08:00:01Z",
        last_timestamp: "2026-07-11T08:00:01Z",
        first_sequence: sequence,
        last_sequence: sequence,
      },
    });
    state = surfaceReducer(state, text(3, "before"));
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: {
        ...lifecycle(4, "warning"),
        event_type: "tool.started",
        payload: {
          kind: "tool.started",
          call_id: "call_1",
          tool_name: "read_file",
          verb: "Read",
        },
      } as EventEnvelope,
    });
    state = surfaceReducer(state, text(5, "after"));

    const assistants = state.active_operation?.turn?.timeline.filter(
      (item) => item.kind === "assistant",
    );
    expect(assistants?.map((item) => item.id)).toEqual([
      "assistant:turn_1:1",
      "assistant:turn_1:2",
    ]);
    expect(new Set(assistants?.map((item) => item.id)).size).toBe(2);
  });

  it("measures only provider-emitted reasoning boundaries", () => {
    let state = surfaceReducer(initialSurfaceState(), {
      type: "event.received",
      generation: 0,
      event: lifecycle(1, "operation.started"),
    });
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: {
        ...lifecycle(2, "turn.started"),
        timestamp: "2026-07-11T08:00:00Z",
      },
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
        text: "considering",
        first_timestamp: "2026-07-11T08:00:01Z",
        last_timestamp: "2026-07-11T08:00:01.500Z",
        first_sequence: 3,
        last_sequence: 3,
      },
    });
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: {
        ...lifecycle(4, "warning"),
        event_type: "tool.started",
        timestamp: "2026-07-11T08:00:02Z",
        payload: {
          kind: "tool.started",
          call_id: "call_1",
          tool_name: "write_file",
          verb: "Write",
          target: "circle_area.py",
        },
      } as EventEnvelope,
    });
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: {
        ...lifecycle(5, "warning"),
        event_type: "tool.completed",
        timestamp: "2026-07-11T08:00:02.018Z",
        payload: {
          kind: "tool.completed",
          call_id: "call_1",
          tool_name: "write_file",
          verb: "Write",
          target: "circle_area.py",
          outcome: "Created",
          summary: "21 lines",
          duration_ms: 18,
        },
      } as EventEnvelope,
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
        text: "done",
        first_timestamp: "2026-07-11T08:00:03Z",
        last_timestamp: "2026-07-11T08:00:03Z",
        first_sequence: 6,
        last_sequence: 6,
      },
    });
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: {
        ...lifecycle(7, "turn.completed"),
        timestamp: "2026-07-11T08:00:05Z",
        payload: {
          kind: "turn.completed",
          reason: "completed",
          duration_ms: 5_000,
        },
      } as EventEnvelope,
    });

    expect(state.active_operation?.turn).toMatchObject({
      duration_ms: 5_000,
      timeline: [
        { kind: "thinking", duration_ms: 1_000 },
        {
          kind: "tool",
          started_at: "2026-07-11T08:00:02Z",
          outcome: "Created",
          duration_ms: 18,
        },
        { kind: "assistant", text: "done" },
      ],
    });
  });

  it("does not fabricate a Thought marker without reasoning deltas", () => {
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
        delta_kind: "text",
        text: "done",
        first_timestamp: "2026-07-11T08:00:03Z",
        last_timestamp: "2026-07-11T08:00:03Z",
        first_sequence: 3,
        last_sequence: 3,
      },
    });

    expect(
      state.active_operation?.turn?.timeline.filter(
        (item) => item.kind === "thinking",
      ),
    ).toEqual([]);
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

  it("bounds reasoning per interval and retains it at Turn completion", () => {
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
        first_timestamp: "2026-07-11T08:00:01Z",
        last_timestamp: "2026-07-11T08:00:01Z",
        first_sequence: 3,
        last_sequence: 3,
      },
    });
    const interval = state.active_operation?.turn?.timeline.find(
      (item) => item.kind === "thinking",
    );
    expect(
      interval?.kind === "thinking" ? interval.text.length : 0,
    ).toBeLessThanOrEqual(32_000);
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: lifecycle(4, "turn.completed"),
    });
    expect(state.active_operation?.turn).toMatchObject({ duration_ms: 1_000 });
    expect(state.active_operation?.turn?.timeline).toContainEqual(
      expect.objectContaining({ kind: "thinking", duration_ms: 0 }),
    );
  });

  it("counts only warnings with the same code and normalized message", () => {
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
    const changedMessage = {
      ...lifecycle(4, "warning"),
      payload: { kind: "warning", code: "safe", message: "different" },
    } as EventEnvelope;
    const changedCode = {
      ...lifecycle(5, "warning"),
      payload: { kind: "warning", code: "other", message: "warning" },
    } as EventEnvelope;
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: changedMessage,
    });
    state = surfaceReducer(state, {
      type: "event.received",
      generation: 0,
      event: changedCode,
    });
    expect(state.warnings).toEqual([
      expect.objectContaining({ code: "safe", message: "warning", count: 2 }),
      expect.objectContaining({ code: "safe", message: "different", count: 1 }),
      expect.objectContaining({ code: "other", message: "warning", count: 1 }),
    ]);
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
