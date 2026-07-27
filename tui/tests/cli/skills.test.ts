import { describe, expect, it, vi } from "vitest";

import type { SkillCommandIntent } from "../../src/cli/args.js";
import {
  acceptsRemovalConfirmation,
  formatSkillList,
  runSkillCommand,
} from "../../src/cli/skills.js";
import type { ConnectedSurface } from "../../src/surface/controller.js";

function ioHarness() {
  const stdout: string[] = [];
  const stderr: string[] = [];
  return {
    stdout,
    stderr,
    io: {
      writeStdout: (value: string) => stdout.push(value),
      writeStderr: (value: string) => stderr.push(value),
    },
  };
}

function surfaceWith(result: unknown) {
  const request = vi.fn(async () => result);
  return {
    request,
    surface: { request } as unknown as ConnectedSurface,
  };
}

describe("Skill management CLI", () => {
  it("renders a stable bounded human list and neutralizes terminal controls", async () => {
    const value = surfaceWith({
      ok: true,
      value: {
        skills: [
          { name: "alpha", description: "Alpha\nworkflow" },
          { name: "review", description: "Review\u001b[31m safely" },
        ],
      },
    });
    const output = ioHarness();

    await expect(
      runSkillCommand(
        value.surface,
        { kind: "skills", action: "list" },
        output.io,
      ),
    ).resolves.toBe(0);

    expect(value.request).toHaveBeenCalledWith("skill.list", {});
    expect(output.stdout.join("")).toBe(
      "Installed User Skills:\n- alpha: Alpha workflow\n- review: Review [31m safely\n",
    );
    expect(output.stderr).toEqual([]);
  });

  it("reports an empty User catalog without exposing implementation paths", () => {
    expect(formatSkillList([])).toBe("No User Skills are installed.\n");
  });

  it.each([
    [
      {
        kind: "skills",
        action: "install",
        sourcePath: "review.zip",
        replace: false,
      },
      { name: "review", status: "installed" },
      ["skill.install", { source_path: "review.zip", replace: false }],
      "Installed Skill review. Restart Awesome to use this change.\n",
    ],
    [
      {
        kind: "skills",
        action: "install",
        sourcePath: "review",
        replace: true,
      },
      { name: "review", status: "replaced" },
      ["skill.install", { source_path: "review", replace: true }],
      "Replaced Skill review. Restart Awesome to use this change.\n",
    ],
    [
      { kind: "skills", action: "remove", name: "review", yes: true },
      { name: "review", status: "removed" },
      ["skill.remove", { name: "review" }],
      "Removed Skill review. Restart Awesome to use this change.\n",
    ],
  ] as const)("maps %s to one pre-initialize RPC and stable success text", async (intent, result, expectedRequest, expectedOutput) => {
    const value = surfaceWith({ ok: true, value: result });
    const output = ioHarness();

    await expect(
      runSkillCommand(value.surface, intent as SkillCommandIntent, output.io),
    ).resolves.toBe(0);

    expect(value.request).toHaveBeenCalledWith(...expectedRequest);
    expect(output.stdout.join("")).toBe(expectedOutput);
    expect(output.stderr).toEqual([]);
  });

  it("keeps product failures off stdout", async () => {
    const value = surfaceWith({
      ok: false,
      error: {
        code: "invalid_arguments",
        message: "The Skill package is invalid.",
        retryable: false,
        data: { diagnostic_code: "invalid_package" },
      },
    });
    const output = ioHarness();

    await expect(
      runSkillCommand(
        value.surface,
        {
          kind: "skills",
          action: "install",
          sourcePath: "invalid.zip",
          replace: false,
        },
        output.io,
      ),
    ).resolves.toBe(1);

    expect(output.stdout).toEqual([]);
    expect(output.stderr.join("")).toBe("The Skill package is invalid.\n");
  });

  it.each([
    ["y", true],
    [" YES ", true],
    ["", false],
    ["n", false],
    ["anything else", false],
  ])("parses removal confirmation %j", (answer, expected) => {
    expect(acceptsRemovalConfirmation(answer)).toBe(expected);
  });
});
