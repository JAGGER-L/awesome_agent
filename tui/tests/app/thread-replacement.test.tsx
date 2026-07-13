import { describe, expect, it, vi } from "vitest";

import {
  replaceThreadSurface,
  ThreadReplacementError,
} from "../../src/app/use-thread-replacement.js";
import type { CommandController } from "../../src/commands/controller.js";
import { initialSurfaceState } from "../../src/state/reducer.js";
import { createSurfaceStore } from "../../src/state/store.js";

function replacement(threadId: string) {
  return {
    kind: "replacement" as const,
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
    const controller = {
      loadThreadReplacement: vi.fn(async () => replacement("thread_new")),
    } as unknown as CommandController;

    await expect(
      replaceThreadSurface({
        store,
        controller,
        request: {
          threadId: "thread_new",
          expectedGeneration: 0,
          reason: "new",
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

  it("rejects a delayed read after another generation wins", async () => {
    let resolve: ((value: ReturnType<typeof replacement>) => void) | undefined;
    const pending = new Promise<ReturnType<typeof replacement>>((accept) => {
      resolve = accept;
    });
    const store = createSurfaceStore();
    const controller = {
      loadThreadReplacement: vi.fn(async () => await pending),
    } as unknown as CommandController;
    const request = replaceThreadSurface({
      store,
      controller,
      request: {
        threadId: "thread_old",
        expectedGeneration: 0,
        reason: "resume",
      },
    });
    store.dispatch({
      type: "thread.replaced",
      application: { current_thread_id: "thread_new" } as never,
      thread: { view: { thread: { id: "thread_new" } } } as never,
      transcript: [],
      transcript_persisted: true,
    });
    resolve?.(replacement("thread_old"));

    await expect(request).resolves.toEqual({ kind: "stale" });
    expect(store.getState().application?.current_thread_id).toBe("thread_new");
  });

  it("rejects mismatched application and thread identities", async () => {
    const store = createSurfaceStore();
    const controller = {
      loadThreadReplacement: vi.fn(async () => ({
        ...replacement("thread_other"),
        application: { current_thread_id: "thread_expected" },
      })),
    } as unknown as CommandController;

    await expect(
      replaceThreadSurface({
        store,
        controller,
        request: {
          threadId: "thread_expected",
          expectedGeneration: 0,
          reason: "resume",
        },
      }),
    ).rejects.toBeInstanceOf(ThreadReplacementError);
  });
});
