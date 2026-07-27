import { describe, expect, it } from "vitest";

import type { MethodValue } from "../../src/protocol/index.js";
import { createSurfaceStore } from "../../src/state/index.js";

const now = "2026-07-11T08:00:00Z";

function page(title: string, id = "thread_1"): MethodValue["thread.read"] {
  return {
    view: {
      thread: {
        id,
        workspace_key: "workspace_1",
        title,
        title_source: "automatic",
        current_model: "deepseek/deepseek-v4-flash",
        thinking_enabled: true,
        skill_mode: "auto",
        lineage: null,
        created_at: now,
        updated_at: now,
      },
      entries: [],
      turns: [],
      tool_activities: [],
    },
    change_sets: [],
    has_more: false,
  };
}

describe("terminal reconciliation metadata", () => {
  it("installs authoritative Thread metadata with terminal blocks", () => {
    const store = createSurfaceStore();
    store.dispatch({
      type: "hydrate.thread",
      thread: page("New conversation"),
    });

    store.dispatch({
      type: "transcript.reconciled",
      generation: 0,
      operation_id: "operation_1",
      turn_id: "turn_1",
      blocks: [],
      thread: page("calculate cube"),
    });

    expect(store.getState().thread?.view.thread.title).toBe("calculate cube");
  });

  it("ignores metadata for another Thread", () => {
    const store = createSurfaceStore();
    store.dispatch({ type: "hydrate.thread", thread: page("Current") });

    store.dispatch({
      type: "transcript.reconciled",
      generation: 0,
      operation_id: "operation_1",
      turn_id: "turn_1",
      blocks: [],
      thread: page("Other", "thread_2"),
    });

    expect(store.getState().thread?.view.thread.title).toBe("Current");
  });
});
