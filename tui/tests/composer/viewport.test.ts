import { describe, expect, it } from "vitest";

import { graphemeCount } from "../../src/composer/graphemes.js";
import {
  computeViewport,
  MAX_COMPOSER_ROWS,
} from "../../src/composer/viewport.js";

describe("computeViewport", () => {
  it.each([40, 60, 120])("soft-wraps at %i columns", (width) => {
    const value = "x".repeat(width + 1);
    const viewport = computeViewport(value, graphemeCount(value), width);
    expect(viewport.rows).toEqual(["x".repeat(width), "x"]);
  });

  it("counts CJK, emoji, and combining marks by terminal display width", () => {
    const viewport = computeViewport("中😀e\u0301x", 4, 5);
    expect(viewport.rows).toEqual(["中😀e\u0301", "x"]);
  });

  it("shows at most eight rows and follows the cursor", () => {
    const value = Array.from({ length: 12 }, (_, index) => `line-${index}`).join(
      "\n",
    );
    const bottom = computeViewport(value, graphemeCount(value), 40);
    expect(bottom.rows).toHaveLength(MAX_COMPOSER_ROWS);
    expect(bottom.rows.at(-1)).toBe("line-11");
    expect(bottom.hiddenAbove).toBe(true);
    expect(bottom.hiddenBelow).toBe(false);

    const top = computeViewport(value, 0, 40);
    expect(top.rows[0]).toBe("line-0");
    expect(top.hiddenAbove).toBe(false);
    expect(top.hiddenBelow).toBe(true);
  });

  it("keeps an empty input visible and clamps invalid widths", () => {
    expect(computeViewport("", 0, 0)).toMatchObject({
      width: 1,
      rows: [""],
      startRow: 0,
    });
  });
});
