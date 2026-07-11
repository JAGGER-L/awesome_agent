import { describe, expect, it } from "vitest";

import {
  CLI_HELP,
  LaunchArgumentError,
  parseCliIntent,
} from "../../src/cli/args.js";

describe("parseCliIntent", () => {
  it.each([
    [[], { kind: "new" }],
    [["--continue"], { kind: "continue" }],
    [["--resume"], { kind: "resume-picker" }],
    [
      ["--resume", "thread_12345678"],
      { kind: "resume", threadId: "thread_12345678" },
    ],
    [["--version"], { kind: "version" }],
    [["-V"], { kind: "version" }],
    [["--help"], { kind: "help" }],
    [["-h"], { kind: "help" }],
  ] as const)("parses %j", (argv, expected) => {
    expect(parseCliIntent([...argv])).toEqual(expected);
  });

  it.each([
    ["--continue", "--resume"],
    ["--resume", "one", "two"],
    ["--version", "extra"],
    ["--help", "extra"],
    ["--unknown"],
  ])("rejects invalid arguments %j", (...argv) => {
    expect(() => parseCliIntent(argv)).toThrow(LaunchArgumentError);
  });

  it("publishes only the approved awesome launch surface", () => {
    expect(CLI_HELP).toContain("Usage: awesome");
    expect(CLI_HELP).toContain("--continue");
    expect(CLI_HELP).toContain("--resume [thread_id]");
    expect(CLI_HELP).toContain("-V, --version");
    expect(CLI_HELP).toContain("-h, --help");
    expect(CLI_HELP).not.toMatch(
      /awesome-tui|awesome-agent|update|uninstall|server|api/iu,
    );
  });
});
