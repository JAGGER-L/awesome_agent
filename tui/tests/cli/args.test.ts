import { describe, expect, it } from "vitest";

import {
  CLI_HELP,
  LaunchArgumentError,
  parseCliIntent,
  parseLaunchIntent,
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
    [
      ["run", "explain this repository"],
      {
        kind: "run",
        prompt: "explain this repository",
        target: { kind: "new" },
        format: "text",
        trustWorkspace: false,
        allowNetwork: false,
      },
    ],
    [
      [
        "run",
        "fix the failing test",
        "--thread",
        "thread_12345678",
        "--format",
        "json",
        "--trust-workspace",
        "--permission-mode",
        "accept_edits",
        "--allow-network",
      ],
      {
        kind: "run",
        prompt: "fix the failing test",
        target: { kind: "thread", threadId: "thread_12345678" },
        format: "json",
        trustWorkspace: true,
        permissionMode: "accept_edits",
        allowNetwork: true,
      },
    ],
    [
      [
        "run",
        "inspect only",
        "--permission-mode",
        "request_approval",
        "--new",
        "--format",
        "text",
      ],
      {
        kind: "run",
        prompt: "inspect only",
        target: { kind: "new" },
        format: "text",
        trustWorkspace: false,
        permissionMode: "request_approval",
        allowNetwork: false,
      },
    ],
    [
      ["run", "make changes", "--permission-mode", "full_access"],
      {
        kind: "run",
        prompt: "make changes",
        target: { kind: "new" },
        format: "text",
        trustWorkspace: false,
        permissionMode: "full_access",
        allowNetwork: false,
      },
    ],
    [["skills", "list"], { kind: "skills", action: "list" }],
    [
      ["skills", "install", "./review-skill"],
      {
        kind: "skills",
        action: "install",
        sourcePath: "./review-skill",
        replace: false,
      },
    ],
    [
      ["skills", "install", "review-skill.zip", "--replace"],
      {
        kind: "skills",
        action: "install",
        sourcePath: "review-skill.zip",
        replace: true,
      },
    ],
    [
      ["skills", "remove", "review-skill"],
      { kind: "skills", action: "remove", name: "review-skill", yes: false },
    ],
    [
      ["skills", "remove", "review-skill", "--yes"],
      { kind: "skills", action: "remove", name: "review-skill", yes: true },
    ],
  ] as const)("parses %j", (argv, expected) => {
    expect(parseCliIntent([...argv])).toEqual(expected);
  });

  it.each([
    ["--continue", "--resume"],
    ["--resume", "one", "two"],
    ["--version", "extra"],
    ["--help", "extra"],
    ["--unknown"],
    ["run"],
    ["run", ""],
    ["run", "   "],
    ["run", "--new"],
    ["run", "prompt", "unexpected"],
    ["run", "prompt", "--unknown"],
    ["run", "prompt", "--new", "--new"],
    ["run", "prompt", "--new", "--thread", "thread_1"],
    ["run", "prompt", "--thread", "thread_1", "--thread", "thread_2"],
    ["run", "prompt", "--thread"],
    ["run", "prompt", "--thread", ""],
    ["run", "prompt", "--thread", "   "],
    ["run", "prompt", "--thread", "--format"],
    ["run", "prompt", "--format"],
    ["run", "prompt", "--format", "yaml"],
    ["run", "prompt", "--format", "text", "--format", "json"],
    ["run", "prompt", "--trust-workspace", "--trust-workspace"],
    ["run", "prompt", "--permission-mode"],
    ["run", "prompt", "--permission-mode", "unsafe"],
    [
      "run",
      "prompt",
      "--permission-mode",
      "accept_edits",
      "--permission-mode",
      "full_access",
    ],
    ["run", "prompt", "--allow-network", "--allow-network"],
    ["skills"],
    ["skills", "unknown"],
    ["skills", "list", "extra"],
    ["skills", "install"],
    ["skills", "install", "--replace"],
    ["skills", "install", ""],
    ["skills", "install", " skill"],
    ["skills", "install", "skill\npath"],
    ["skills", "install", "skill", "--replace", "--replace"],
    ["skills", "install", "skill", "--yes"],
    ["skills", "remove"],
    ["skills", "remove", "Review"],
    ["skills", "remove", "review_skill"],
    ["skills", "remove", "review", "--yes", "--yes"],
    ["skills", "remove", "review", "--replace"],
  ])("rejects invalid arguments %j", (...argv) => {
    expect(() => parseCliIntent(argv)).toThrow(LaunchArgumentError);
  });

  it("publishes only the approved awesome launch surface", () => {
    expect(CLI_HELP).toContain("Usage: awesome");
    expect(CLI_HELP).toContain("--continue");
    expect(CLI_HELP).toContain("--resume [thread_id]");
    expect(CLI_HELP).toContain("awesome run <prompt>");
    expect(CLI_HELP).toContain("--new");
    expect(CLI_HELP).toContain("--thread <id>");
    expect(CLI_HELP).toContain("--format <text|json>");
    expect(CLI_HELP).toContain("--trust-workspace");
    expect(CLI_HELP).toContain("--permission-mode");
    expect(CLI_HELP).toContain("--allow-network");
    expect(CLI_HELP).toContain("awesome skills list");
    expect(CLI_HELP).toContain(
      "awesome skills install <local-directory-or-zip> [--replace]",
    );
    expect(CLI_HELP).toContain("awesome skills remove <name> [--yes]");
    expect(CLI_HELP).toContain("-V, --version");
    expect(CLI_HELP).toContain("-h, --help");
    expect(CLI_HELP).not.toMatch(
      /awesome-tui|awesome-agent|update|uninstall|server|api/iu,
    );
  });

  it("bounds prompt and Thread identity before Core startup", () => {
    expect(() => parseCliIntent(["run", "x".repeat(200_001)])).toThrow(
      LaunchArgumentError,
    );
    expect(() =>
      parseCliIntent(["run", "prompt", "--thread", "t".repeat(129)]),
    ).toThrow(LaunchArgumentError);
    expect(() =>
      parseCliIntent(["skills", "install", "s".repeat(4_097)]),
    ).toThrow(LaunchArgumentError);
  });

  it("does not expose headless execution as an interactive LaunchIntent", () => {
    expect(() => parseLaunchIntent(["run", "prompt"])).toThrow(
      LaunchArgumentError,
    );
    expect(() => parseLaunchIntent(["skills", "list"])).toThrow(
      LaunchArgumentError,
    );
  });
});
