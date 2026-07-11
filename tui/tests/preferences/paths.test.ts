import { describe, expect, it } from "vitest";

import { resolveAwesomeHome } from "../../src/preferences/paths.js";

describe("resolveAwesomeHome", () => {
  it.each([
    {
      name: "AWESOME_HOME override",
      input: {
        environ: {
          AWESOME_HOME: "D:\\awesome-data",
          LOCALAPPDATA: "C:\\Local",
        },
        home: "C:\\Users\\dev",
        platform: "win32" as const,
      },
      expected: "D:\\awesome-data",
    },
    {
      name: "Windows LOCALAPPDATA",
      input: {
        environ: { LOCALAPPDATA: "C:\\Users\\dev\\AppData\\Local" },
        home: "C:\\Users\\dev",
        platform: "win32" as const,
      },
      expected: "C:\\Users\\dev\\AppData\\Local\\Awesome",
    },
    {
      name: "Windows home fallback",
      input: {
        environ: {},
        home: "C:\\Users\\dev",
        platform: "win32" as const,
      },
      expected: "C:\\Users\\dev\\AppData\\Local\\Awesome",
    },
    {
      name: "POSIX home",
      input: { environ: {}, home: "/home/dev", platform: "linux" as const },
      expected: "/home/dev/.awesome",
    },
    {
      name: "empty override",
      input: {
        environ: { AWESOME_HOME: "   " },
        home: "/home/dev",
        platform: "linux" as const,
      },
      expected: "/home/dev/.awesome",
    },
    {
      name: "tilde override",
      input: {
        environ: { AWESOME_HOME: "~/agent-data" },
        home: "/home/dev",
        platform: "linux" as const,
      },
      expected: "/home/dev/agent-data",
    },
  ])("matches Python path behavior for $name", ({ input, expected }) => {
    expect(resolveAwesomeHome(input)).toBe(expected);
  });
});
