import { describe, expect, it } from "vitest";

import { adaptInkCursorPosition } from "../../src/components/cursor/ink-cursor-bridge.js";

describe("Ink cursor bridge", () => {
  const logical = { x: 12, y: 18 } as const;

  it("keeps the logical row below the fullscreen threshold", () => {
    expect(
      adaptInkCursorPosition(logical, {
        frameHeight: 23,
        terminalRows: 24,
        hasMeasured: true,
      }),
    ).toEqual(logical);
  });

  it.each([24, 25])(
    "adapts the physical row when frame height is %i",
    (frameHeight) => {
      expect(
        adaptInkCursorPosition(logical, {
          frameHeight,
          terminalRows: 24,
          hasMeasured: true,
        }),
      ).toEqual({ x: 12, y: 19 });
    },
  );

  it("hides the cursor until both frame and cursor are available", () => {
    expect(
      adaptInkCursorPosition(logical, {
        frameHeight: 0,
        terminalRows: 24,
        hasMeasured: false,
      }),
    ).toBeUndefined();
    expect(
      adaptInkCursorPosition(undefined, {
        frameHeight: 10,
        terminalRows: 24,
        hasMeasured: true,
      }),
    ).toBeUndefined();
  });
});
