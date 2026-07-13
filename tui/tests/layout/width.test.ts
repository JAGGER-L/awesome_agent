import { describe, expect, it } from "vitest";

import { terminalDisplayWidth } from "../../src/layout/width.js";

describe("terminalDisplayWidth", () => {
  it("measures ASCII, CJK, and emoji in terminal cells", () => {
    expect(terminalDisplayWidth("awesome")).toBe(7);
    expect(terminalDisplayWidth("模型")).toBe(4);
    expect(terminalDisplayWidth("A😀B")).toBe(4);
  });
});
