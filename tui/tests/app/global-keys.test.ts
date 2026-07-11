import { describe, expect, it } from "vitest";

import { GlobalKeyController } from "../../src/app/global-keys.js";

const ctrl = (value: string) => ({ input: value, key: { ctrl: true } });

describe("GlobalKeyController", () => {
  it("maps active Ctrl+C only to cancellation", () => {
    const keys = new GlobalKeyController(() => 0);
    expect(
      keys.handle({
        ...ctrl("c"),
        activeOperation: true,
        composerEmpty: false,
      }),
    ).toEqual({ kind: "cancel" });
    expect(
      keys.handle({
        input: "c",
        key: { ctrl: false },
        activeOperation: true,
        composerEmpty: true,
      }),
    ).toBeUndefined();
  });

  it("clears only non-empty idle input on Ctrl+C", () => {
    const keys = new GlobalKeyController(() => 0);
    expect(
      keys.handle({
        ...ctrl("c"),
        activeOperation: false,
        composerEmpty: false,
      }),
    ).toEqual({ kind: "clear_composer" });
  });

  it("requires two empty idle Ctrl+C presses within two seconds", () => {
    let now = 1_000;
    const keys = new GlobalKeyController(() => now);
    const context = {
      ...ctrl("c"),
      activeOperation: false,
      composerEmpty: true,
    };
    expect(keys.handle(context)).toEqual({ kind: "exit_hint" });
    now = 2_999;
    expect(keys.handle(context)).toEqual({
      kind: "exit",
      reason: "double_ctrl_c",
    });
  });

  it("restarts an expired Ctrl+C window", () => {
    let now = 1_000;
    const keys = new GlobalKeyController(() => now);
    const context = {
      ...ctrl("c"),
      activeOperation: false,
      composerEmpty: true,
    };
    expect(keys.handle(context)?.kind).toBe("exit_hint");
    now = 3_001;
    expect(keys.handle(context)?.kind).toBe("exit_hint");
  });

  it("maps Ctrl+D only when the composer is empty", () => {
    const keys = new GlobalKeyController(() => 0);
    expect(
      keys.handle({
        ...ctrl("d"),
        activeOperation: false,
        composerEmpty: true,
      }),
    ).toEqual({ kind: "exit", reason: "ctrl_d" });
    expect(
      keys.handle({
        ...ctrl("d"),
        activeOperation: false,
        composerEmpty: false,
      }),
    ).toBeUndefined();
  });

  it("does not let active Ctrl+C enter the double-exit window", () => {
    let now = 1_000;
    const keys = new GlobalKeyController(() => now);
    expect(
      keys.handle({
        ...ctrl("c"),
        activeOperation: true,
        composerEmpty: true,
      })?.kind,
    ).toBe("cancel");
    now = 1_100;
    expect(
      keys.handle({
        ...ctrl("c"),
        activeOperation: false,
        composerEmpty: true,
      })?.kind,
    ).toBe("exit_hint");
  });
});
