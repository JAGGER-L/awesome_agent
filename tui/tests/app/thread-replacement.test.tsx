import { describe, expect, it, vi } from "vitest";

import {
  replaceThreadSurface,
  ThreadReplacementError,
} from "../../src/app/use-thread-replacement.js";
import { initialSurfaceState } from "../../src/state/reducer.js";
import { createSurfaceStore } from "../../src/state/store.js";

function transition(threadId: string, reason: "new" | "resume" = "new") {
  return {
    reason,
    application: { current_thread_id: threadId } as never,
    thread: {
      has_more: false,
      view: {
        thread: { id: threadId },
        entries: [],
        turns: [],
        tool_activities: [],
      },
      change_sets: [],
    } as never,
  };
}

describe("replaceThreadSurface", () => {
  it("atomically installs one new-conversation notice and resets lifecycle", async () => {
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      committed_transcript: [{ key: "old", kind: "status", message: "old" }],
    });
    const reset = vi.fn();
    await expect(
      replaceThreadSurface({
        store,
        request: {
          transition: transition("thread_new"),
          expectedGeneration: 0,
        },
        resetThreadScope: reset,
      }),
    ).resolves.toEqual({ kind: "replaced", generation: 1 });

    expect(store.getState().committed_transcript).toEqual([
      {
        key: "thread-start:thread_new",
        kind: "status",
        message: "New conversation started",
      },
    ]);
    expect(store.getState().transcript_persisted).toBe(false);
    expect(reset).toHaveBeenCalledOnce();
  });

  it("rejects a stale transition after another generation wins", async () => {
    const store = createSurfaceStore();
    store.dispatch({
      type: "thread.replaced",
      application: { current_thread_id: "thread_new" } as never,
      thread: { view: { thread: { id: "thread_new" } } } as never,
      transcript: [],
      transcript_persisted: true,
    });
    await expect(
      replaceThreadSurface({
        store,
        request: {
          transition: transition("thread_old", "resume"),
          expectedGeneration: 0,
        },
      }),
    ).resolves.toEqual({ kind: "stale" });
    expect(store.getState().application?.current_thread_id).toBe("thread_new");
  });

  it("rejects mismatched application and thread identities", async () => {
    const store = createSurfaceStore();
    const invalid = {
      ...transition("thread_other", "resume"),
      application: { current_thread_id: "thread_expected" },
    } as never;

    await expect(
      replaceThreadSurface({
        store,
        request: {
          transition: invalid,
          expectedGeneration: 0,
        },
      }),
    ).rejects.toBeInstanceOf(ThreadReplacementError);
  });
});
