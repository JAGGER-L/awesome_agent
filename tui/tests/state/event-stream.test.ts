import { describe, expect, it } from "vitest";

import type { EventEnvelope } from "../../src/protocol/index.js";
import {
  DeltaBatcher,
  type ScheduledTask,
} from "../../src/state/delta-batcher.js";
import {
  EventStreamGuard,
  ProtocolDesynchronized,
} from "../../src/state/event-stream.js";

function event(
  sequence: number,
  kind:
    | "assistant.text.delta"
    | "assistant.reasoning.delta"
    | "warning" = "warning",
  overrides: Partial<EventEnvelope> = {},
): EventEnvelope {
  const payload =
    kind === "warning"
      ? { kind, code: "safe", message: "warning" }
      : { kind, text: String(sequence) };
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
    ...overrides,
  } as EventEnvelope;
}

class ManualScheduler {
  tasks: { callback: () => void; delay: number; cancelled: boolean }[] = [];

  schedule(callback: () => void, delay: number): ScheduledTask {
    const task = { callback, delay, cancelled: false };
    this.tasks.push(task);
    return { cancel: () => (task.cancelled = true) };
  }

  run(): void {
    const task = this.tasks.shift();
    if (task && !task.cancelled) task.callback();
  }
}

describe("EventStreamGuard", () => {
  it("accepts a contiguous Session beginning at one", () => {
    const guard = new EventStreamGuard();
    expect(guard.accept(event(1))).toBeUndefined();
    expect(guard.accept(event(2))).toBeUndefined();
  });

  it.each([
    ["first sequence", event(2)],
    ["duplicate", event(1)],
    ["gap", event(3)],
    ["Session change", event(2, "warning", { session_id: "session_2" })],
    ["unsafe sequence", event(Number.MAX_SAFE_INTEGER + 1)],
  ])("closes on %s", (_name, invalid) => {
    const guard = new EventStreamGuard();
    if (invalid.sequence !== 2 || invalid.session_id !== "session_1")
      guard.accept(event(1));
    const fault = guard.accept(invalid);
    expect(fault).toBeInstanceOf(ProtocolDesynchronized);
    expect(guard.accept(event(2))).toBe(fault);
  });

  it("accepts a new Session only after explicit reset", () => {
    const guard = new EventStreamGuard();
    guard.accept(event(1));
    expect(
      guard.accept(event(2, "warning", { session_id: "session_2" })),
    ).toBeInstanceOf(ProtocolDesynchronized);
    guard.reset();
    expect(
      guard.accept(event(1, "warning", { session_id: "session_2" })),
    ).toBeUndefined();
    guard.close();
    expect(
      guard.accept(event(2, "warning", { session_id: "session_2" })),
    ).toBeInstanceOf(ProtocolDesynchronized);
  });
});

describe("DeltaBatcher", () => {
  it("coalesces adjacent matching text deltas for one 16 ms tick", () => {
    const scheduler = new ManualScheduler();
    const output: unknown[] = [];
    const batcher = new DeltaBatcher(
      new EventStreamGuard(),
      (value) => output.push(value),
      scheduler,
    );
    batcher.accept(event(1, "assistant.text.delta"));
    batcher.accept(event(2, "assistant.text.delta"));
    expect(output).toEqual([]);
    expect(scheduler.tasks[0]?.delay).toBe(16);
    scheduler.run();
    expect(output).toEqual([
      expect.objectContaining({
        kind: "coalesced_delta",
        delta_kind: "text",
        text: "12",
        first_sequence: 1,
        last_sequence: 2,
      }),
    ]);
  });

  it("never batches across kind, identity, or structural boundaries", () => {
    const scheduler = new ManualScheduler();
    const output: unknown[] = [];
    const batcher = new DeltaBatcher(
      new EventStreamGuard(),
      (value) => output.push(value),
      scheduler,
    );
    batcher.accept(event(1, "assistant.text.delta"));
    batcher.accept(event(2, "assistant.reasoning.delta"));
    batcher.accept(
      event(3, "assistant.reasoning.delta", { turn_id: "turn_2" }),
    );
    batcher.accept(event(4, "warning"));
    expect(
      output.map(
        (value) =>
          (value as { kind?: string; event_type?: string }).kind ??
          (value as EventEnvelope).event_type,
      ),
    ).toEqual([
      "coalesced_delta",
      "coalesced_delta",
      "coalesced_delta",
      "warning",
    ]);
  });

  it("bounds a batch even when many deltas arrive within one tick", () => {
    const scheduler = new ManualScheduler();
    const output: { text: string }[] = [];
    const batcher = new DeltaBatcher(
      new EventStreamGuard(),
      (value) => {
        if ("kind" in value) output.push(value);
      },
      scheduler,
    );
    batcher.accept(
      event(1, "assistant.text.delta", {
        payload: { kind: "assistant.text.delta", text: "a".repeat(6_000) },
      } as Partial<EventEnvelope>),
    );
    batcher.accept(
      event(2, "assistant.text.delta", {
        payload: { kind: "assistant.text.delta", text: "b".repeat(6_000) },
      } as Partial<EventEnvelope>),
    );
    batcher.close();

    expect(output.map((item) => item.text.length)).toEqual([6_000, 6_000]);
  });

  it("flushes once on close and reports sequence faults", () => {
    const scheduler = new ManualScheduler();
    const output: unknown[] = [];
    const batcher = new DeltaBatcher(
      new EventStreamGuard(),
      (value) => output.push(value),
      scheduler,
    );
    batcher.accept(event(1, "assistant.text.delta"));
    batcher.close();
    batcher.close();
    expect(output).toHaveLength(1);
    expect(batcher.accept(event(2))).toBeInstanceOf(ProtocolDesynchronized);
  });
});
