import { describe, expect, it } from "vitest";

import { mapGlobalKey } from "../../src/app/global-keys.js";

describe("mapGlobalKey", () => {
  it("maps active Ctrl+C only to cancellation", () => {
    expect(
      mapGlobalKey({
        input: "c",
        key: { ctrl: true },
        activeOperation: true,
        composerEmpty: false,
      }),
    ).toEqual({ kind: "cancel" });
  });

  it("does not treat ordinary c or idle Ctrl+C as active cancellation", () => {
    expect(
      mapGlobalKey({
        input: "c",
        key: { ctrl: false },
        activeOperation: true,
        composerEmpty: true,
      }),
    ).toBeUndefined();
    expect(
      mapGlobalKey({
        input: "c",
        key: { ctrl: true },
        activeOperation: false,
        composerEmpty: true,
      }),
    ).toBeUndefined();
  });
});
