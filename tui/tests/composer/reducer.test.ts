import { describe, expect, it } from "vitest";

import { graphemeCount, graphemes } from "../../src/composer/graphemes.js";
import {
  composerReducer,
  initialComposerState,
  MAX_COMPOSER_CODE_POINTS,
} from "../../src/composer/reducer.js";

function insert(text: string) {
  return composerReducer(initialComposerState(), { type: "insert", text });
}

describe("composerReducer", () => {
  it.each([
    "abc",
    "中文",
    "e\u0301",
    "👨‍👩‍👧‍👦",
    "🇨🇳",
    "👍🏽",
  ])("edits %s atomically by grapheme", (text) => {
    let state = insert(text);
    expect(state.cursorGrapheme).toBe(graphemeCount(text));
    state = composerReducer(state, { type: "backspace" });
    expect(state.value).toBe(graphemes(text).slice(0, -1).join(""));
  });

  it("inserts and deletes at grapheme boundaries", () => {
    let state = insert("a😀c");
    state = composerReducer(state, { type: "left" });
    state = composerReducer(state, { type: "insert", text: "中" });
    expect(state.value).toBe("a😀中c");
    state = composerReducer(state, { type: "backspace" });
    state = composerReducer(state, { type: "delete" });
    expect(state.value).toBe("a😀");
  });

  it("supports logical line and complete-buffer controls", () => {
    let state = insert("one two\nthree four");
    state = composerReducer(state, { type: "home" });
    expect(state.cursorGrapheme).toBe(8);
    state = composerReducer(state, { type: "end" });
    expect(state.cursorGrapheme).toBe(graphemeCount(state.value));
    state = composerReducer(state, { type: "delete_word" });
    expect(state.value).toBe("one two\nthree ");
    state = composerReducer(state, { type: "delete_line_start" });
    expect(state.value).toBe("one two\n");
    state = composerReducer(state, { type: "buffer_home" });
    state = composerReducer(state, { type: "delete_line_end" });
    expect(state.value).toBe("\n");
  });

  it("rejects an over-limit emoji paste without partial insertion", () => {
    const exact = insert("😀".repeat(MAX_COMPOSER_CODE_POINTS));
    expect(exact.error).toBeUndefined();
    const rejected = composerReducer(exact, { type: "insert", text: "x" });
    expect(rejected.value).toBe(exact.value);
    expect(rejected.cursorGrapheme).toBe(exact.cursorGrapheme);
    expect(rejected.error).toBe("input_too_large");
  });
});
