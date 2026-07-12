import { describe, expect, it } from "vitest";

import {
  composerReducer,
  initialComposerState,
} from "../../src/composer/reducer.js";

function reduce(
  state: ReturnType<typeof initialComposerState>,
  action: Parameters<typeof composerReducer>[1],
) {
  return composerReducer(state, action);
}

describe("composer history", () => {
  it("restores multiline entries and then the current draft", () => {
    let state = initialComposerState();
    state = reduce(state, { type: "submit_history", value: "first\nturn" });
    state = reduce(state, { type: "submit_history", value: "second" });
    state = reduce(state, { type: "replace", value: "draft" });
    state = reduce(state, { type: "history_previous" });
    expect(state.value).toBe("second");
    state = reduce(state, { type: "history_previous" });
    expect(state.value).toBe("first\nturn");
    state = reduce(state, { type: "history_next" });
    state = reduce(state, { type: "history_next" });
    expect(state.value).toBe("draft");
    expect(state.historyIndex).toBeNull();
  });

  it("skips empty and consecutive duplicate submissions", () => {
    let state = initialComposerState();
    for (const value of ["", "one", "one", "two"]) {
      state = reduce(state, { type: "submit_history", value });
    }
    expect(state.history).toEqual(["one", "two"]);
  });

  it("keeps at most fifty entries", () => {
    let state = initialComposerState();
    for (let index = 0; index < 55; index += 1) {
      state = reduce(state, { type: "submit_history", value: `turn-${index}` });
    }
    expect(state.history).toHaveLength(50);
    expect(state.history[0]).toBe("turn-5");
  });

  it("keeps at most one MiB of UTF-8 data", () => {
    let state = initialComposerState();
    const chunk = "中".repeat(180_000);
    for (let index = 0; index < 3; index += 1) {
      state = reduce(state, {
        type: "submit_history",
        value: `${index}${chunk}`,
      });
    }
    const bytes = state.history.reduce(
      (sum, value) => sum + Buffer.byteLength(value, "utf8"),
      0,
    );
    expect(bytes).toBeLessThanOrEqual(1024 * 1024);
    expect(state.history).toHaveLength(1);
  });

  it("reflows the viewport when resized", () => {
    let state = initialComposerState();
    state = reduce(state, { type: "replace", value: "x".repeat(81) });
    state = reduce(state, { type: "resize", width: 40 });
    expect(state.viewport.width).toBe(40);
    expect(state.viewport.rows).toHaveLength(3);
  });
});
