import { describe, expect, it } from "vitest";

import {
  detectColorCapability,
  resolveTheme,
} from "../../src/preferences/theme.js";

describe("detectColorCapability", () => {
  it("honors capability precedence", () => {
    expect(
      detectColorCapability(
        { NO_COLOR: "1", FORCE_COLOR: "3", COLORTERM: "truecolor" },
        true,
      ),
    ).toBe("none");
    expect(detectColorCapability({ FORCE_COLOR: "3" }, false)).toBe(
      "truecolor",
    );
    expect(detectColorCapability({ FORCE_COLOR: "2" }, false)).toBe("ansi256");
    expect(detectColorCapability({ FORCE_COLOR: "1" }, false)).toBe("ansi16");
    expect(detectColorCapability({ COLORTERM: "truecolor" }, true)).toBe(
      "truecolor",
    );
    expect(detectColorCapability({ TERM: "xterm-256color" }, true)).toBe(
      "ansi256",
    );
    expect(detectColorCapability({}, true)).toBe("ansi16");
    expect(detectColorCapability({}, false)).toBe("none");
  });
});

describe("resolveTheme", () => {
  it("uses the accepted exact dark Mint rows in TrueColor", () => {
    expect(resolveTheme("dark", "truecolor").logoRows).toEqual([
      "#A7F3D0",
      "#6EE7B7",
      "#34D399",
      "#2DD4BF",
      "#22D3EE",
    ]);
  });

  it("maps Mint into xterm cube and 16-color roles", () => {
    expect(resolveTheme("dark", "ansi256").logoRows).toEqual([
      "#AFFFD7",
      "#5FD7AF",
      "#5FD787",
      "#00D7AF",
      "#00D7FF",
    ]);
    expect(resolveTheme("dark", "ansi16").logoRows).toEqual([
      "greenBright",
      "greenBright",
      "green",
      "cyan",
      "cyanBright",
    ]);
  });

  it("uses named system colors and default foreground without color support", () => {
    expect(resolveTheme("system", "ansi16").logoRows).toEqual([
      "greenBright",
      "green",
      "green",
      "cyan",
      "cyanBright",
    ]);
    expect(resolveTheme("dark", "none").logoRows).toEqual([
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
    ]);
  });
});
