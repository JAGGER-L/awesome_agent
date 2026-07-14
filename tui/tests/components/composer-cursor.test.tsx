import { describe, expect, it } from "vitest";

import { graphemes } from "../../src/composer/graphemes.js";
import { displayWidth } from "../../src/composer/viewport.js";
import { resolveComposerCursorPosition } from "../../src/components/use-composer-cursor.js";

const measured = {
  left: 4,
  top: 10,
  width: 40,
  height: 4,
  hasMeasured: true,
};

function textWidth(value: string): number {
  return graphemes(value).reduce(
    (width, grapheme) => width + displayWidth(grapheme),
    0,
  );
}

describe("Composer cursor position", () => {
  it("positions the real cursor after wide input", () => {
    const draftBeforeCursor = "你好e\u0301👩‍💻";
    expect(
      resolveComposerCursorPosition({
        active: true,
        metrics: measured,
        cursorRow: 0,
        cursorColumn: textWidth(draftBeforeCursor),
        hiddenAbove: false,
      }),
    ).toEqual({
      x: measured.left + 2 + textWidth(`❯ ${draftBeforeCursor}`),
      y: 12,
    });
  });

  it("accounts for wrapped rows and the hidden-above marker", () => {
    expect(
      resolveComposerCursorPosition({
        active: true,
        metrics: measured,
        cursorRow: 3,
        cursorColumn: 5,
        hiddenAbove: true,
      }),
    ).toEqual({ x: 13, y: 16 });
  });

  it("hides the cursor before measurement or without Composer ownership", () => {
    expect(
      resolveComposerCursorPosition({
        active: false,
        metrics: measured,
        cursorRow: 0,
        cursorColumn: 0,
        hiddenAbove: false,
      }),
    ).toBeUndefined();
    expect(
      resolveComposerCursorPosition({
        active: true,
        metrics: { ...measured, hasMeasured: false },
        cursorRow: 0,
        cursorColumn: 0,
        hiddenAbove: false,
      }),
    ).toBeUndefined();
  });

  it("recomputes the anchor when terminal layout metrics change", () => {
    const options = {
      active: true,
      metrics: measured,
      cursorRow: 1,
      cursorColumn: 7,
      hiddenAbove: false,
    } as const;
    expect(resolveComposerCursorPosition(options)).toEqual({ x: 15, y: 13 });
    expect(
      resolveComposerCursorPosition({
        ...options,
        metrics: { ...measured, left: 1, top: 2, width: 24 },
      }),
    ).toEqual({ x: 12, y: 5 });
  });

  it("hides the cursor while the Composer is submitting", () => {
    expect(
      resolveComposerCursorPosition({
        active: false,
        metrics: measured,
        cursorRow: 0,
        cursorColumn: 4,
        hiddenAbove: false,
      }),
    ).toBeUndefined();
  });
});
