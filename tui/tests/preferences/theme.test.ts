import { describe, expect, it } from "vitest";

import {
  detectColorCapability,
  resolveTheme,
} from "../../src/preferences/theme.js";

const darkAurora = {
  logoRows: ["#D0F5E7", "#B0EADF", "#95DCDA", "#8BC9E5", "#96B5DF"],
  brand: "#A9EADC",
  primary: "#9BE4D6",
  secondary: "#8FC8E8",
  border: "#5EA9AA",
  user: "#B8EADF",
  tool: "#88C4E2",
  assistant: "#E5ECEF",
  muted: "#74838B",
} as const;

const lightAurora = {
  logoRows: ["#2B7A70", "#337F7D", "#3D7E8C", "#3C7290", "#4B6290"],
  brand: "#2B7A70",
  primary: "#26766B",
  secondary: "#326B8A",
  border: "#4B7C7B",
  user: "#256F66",
  tool: "#316B86",
  assistant: "#1B242B",
  muted: "#5B6870",
} as const;

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

  it("uses observed stdout depth before terminal-name fallbacks", () => {
    expect(detectColorCapability({}, true, 24)).toBe("truecolor");
    expect(detectColorCapability({}, true, 8)).toBe("ansi256");
    expect(detectColorCapability({}, true, 4)).toBe("ansi16");
  });

  it("recognizes Windows Terminal when depth is unavailable", () => {
    expect(detectColorCapability({ WT_SESSION: "session" }, true)).toBe(
      "truecolor",
    );
  });

  it("keeps explicit no-color and force-color precedence", () => {
    expect(
      detectColorCapability(
        { NO_COLOR: "1", FORCE_COLOR: "3", WT_SESSION: "session" },
        true,
        24,
      ),
    ).toBe("none");
    expect(detectColorCapability({ FORCE_COLOR: "1" }, true, 24)).toBe(
      "ansi16",
    );
  });
});

describe("resolveTheme", () => {
  it("exposes one complete semantic color contract", () => {
    expect(Object.keys(resolveTheme("dark", "truecolor")).sort()).toEqual(
      [
        "assistant",
        "border",
        "brand",
        "colorEnabled",
        "danger",
        "logoRows",
        "muted",
        "preference",
        "primary",
        "secondary",
        "statusBackground",
        "success",
        "tool",
        "user",
        "warning",
      ].sort(),
    );
  });

  it("uses the approved Aurora Mist TrueColor roles", () => {
    const dark = resolveTheme("dark", "truecolor");
    const light = resolveTheme("light", "truecolor");
    expect(dark.logoRows).toEqual(darkAurora.logoRows);
    expect(dark).toMatchObject(darkAurora);
    expect(light.logoRows).toEqual(lightAurora.logoRows);
    expect(light).toMatchObject(lightAurora);
  });

  it("keeps every light text role at WCAG AA contrast on white", () => {
    for (const value of [
      lightAurora.brand,
      lightAurora.primary,
      lightAurora.secondary,
      lightAurora.border,
      lightAurora.user,
      lightAurora.tool,
      lightAurora.assistant,
      lightAurora.muted,
    ]) {
      expect(contrastOnWhite(value)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("maps Aurora Mist into xterm cube and 16-color roles", () => {
    expect(resolveTheme("dark", "ansi256").logoRows).toEqual([
      "#D7FFD7",
      "#AFD7D7",
      "#87D7D7",
      "#87D7D7",
      "#87AFD7",
    ]);
    expect(resolveTheme("dark", "ansi16").logoRows).toEqual([
      "greenBright",
      "greenBright",
      "green",
      "cyan",
      "cyanBright",
    ]);
  });

  it("uses capability colors for system and disables Logo color without color", () => {
    expect(resolveTheme("system", "ansi16").logoRows).toEqual([
      "greenBright",
      "greenBright",
      "green",
      "cyan",
      "cyanBright",
    ]);
    const none = resolveTheme("dark", "none");
    expect(none.logoRows).toEqual([
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
    ]);
  });
});

function contrastOnWhite(value: string): number {
  const channels = [1, 3, 5].map((offset) =>
    Number.parseInt(value.slice(offset, offset + 2), 16),
  );
  const luminance = channels
    .map((channel) => channel / 255)
    .map((channel) =>
      channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
    )
    .reduce(
      (sum, channel, index) =>
        sum + channel * ([0.2126, 0.7152, 0.0722][index] ?? 0),
      0,
    );
  return 1.05 / (luminance + 0.05);
}
