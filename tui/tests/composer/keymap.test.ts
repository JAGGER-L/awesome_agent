import { describe, expect, it } from "vitest";

import { mapComposerKey } from "../../src/composer/keymap.js";

const key = {
  upArrow: false,
  downArrow: false,
  leftArrow: false,
  rightArrow: false,
  pageDown: false,
  pageUp: false,
  home: false,
  end: false,
  return: false,
  escape: false,
  ctrl: false,
  shift: false,
  tab: false,
  backspace: false,
  delete: false,
  meta: false,
  super: false,
  hyper: false,
  capsLock: false,
  numLock: false,
};

describe("mapComposerKey", () => {
  it("submits Enter and inserts a newline for Ctrl+J", () => {
    expect(mapComposerKey("", { ...key, return: true }, false)).toEqual({
      type: "submit",
    });
    expect(
      mapComposerKey("j", { ...key, ctrl: true, return: true }, false),
    ).toEqual({ type: "edit", action: { type: "insert", text: "\n" } });
  });

  it("treats Shift+Enter as a best-effort newline", () => {
    expect(
      mapComposerKey("", { ...key, shift: true, return: true }, false),
    ).toEqual({ type: "edit", action: { type: "insert", text: "\n" } });
  });

  it("uses history arrows only when the composer is empty", () => {
    expect(mapComposerKey("", { ...key, upArrow: true }, true)).toEqual({
      type: "edit",
      action: { type: "history_previous" },
    });
    expect(
      mapComposerKey("", { ...key, upArrow: true }, false),
    ).toBeUndefined();
  });
});
