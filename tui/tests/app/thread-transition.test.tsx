import { describe, expect, it, vi } from "vitest";

import { applyThreadTransition } from "../../src/app/use-thread-transition.js";
import { initialSurfaceState } from "../../src/state/reducer.js";
import { createSurfaceStore } from "../../src/state/store.js";

function transition(
  threadId: string,
  reason: "new" | "resume" = "new",
  entries: readonly unknown[] = [],
) {
  return {
    reason,
    application: { current_thread_id: threadId } as never,
    thread: {
      has_more: false,
      view: {
        thread: { id: threadId },
        entries,
        turns: [],
        tool_activities: [],
      },
      change_sets: [],
    } as never,
  };
}

describe("applyThreadTransition", () => {
  it("replaces state before resetting exactly one current Ink frame", () => {
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      committed_transcript: [{ key: "old", kind: "status", message: "old" }],
    });
    const order: string[] = [];
    const resetCurrentFrame = vi.fn(() => {
      expect(store.getState().thread_generation).toBe(1);
      order.push("frame");
    });

    expect(
      applyThreadTransition({
        store,
        transition: transition("thread_new"),
        expectedGeneration: 0,
        effects: {
          resetThreadScope: () => order.push("scope"),
          resetCurrentFrame,
        },
      }),
    ).toEqual({ kind: "replaced", generation: 1 });

    expect(order).toEqual(["scope", "frame"]);
    expect(resetCurrentFrame).toHaveBeenCalledOnce();
    expect(store.getState().committed_transcript).toEqual([
      {
        key: "thread-start:thread_new",
        kind: "status",
        message: "New conversation started",
      },
    ]);
  });

  it("does not reset state or effects for a stale transition", () => {
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      thread_generation: 2,
    });
    const resetThreadScope = vi.fn();
    const resetCurrentFrame = vi.fn();

    expect(
      applyThreadTransition({
        store,
        transition: transition("thread_old", "resume"),
        expectedGeneration: 1,
        effects: { resetThreadScope, resetCurrentFrame },
      }),
    ).toEqual({ kind: "stale" });

    expect(resetThreadScope).not.toHaveBeenCalled();
    expect(resetCurrentFrame).not.toHaveBeenCalled();
  });

  it("hydrates only the selected resumed Thread", () => {
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      committed_transcript: [
        { key: "old", kind: "assistant", text: "old assistant" },
      ],
    });
    const resumed = transition("thread_resumed", "resume", [
      {
        id: "entry_resumed",
        thread_id: "thread_resumed",
        sequence: 1,
        kind: "assistant_message",
        content: "selected assistant",
        metadata: { citations: [] },
        created_at: "2026-07-13T00:00:00Z",
      },
    ]);

    applyThreadTransition({
      store,
      transition: resumed,
      expectedGeneration: 0,
      effects: { resetCurrentFrame: vi.fn() },
    });

    expect(JSON.stringify(store.getState().committed_transcript)).toContain(
      "selected assistant",
    );
    expect(JSON.stringify(store.getState().committed_transcript)).not.toContain(
      "old assistant",
    );
  });
});
