import { mkdir, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { runCli, type CliDependencies } from "../../src/cli/main.js";
import { connectSurface } from "../../src/surface/controller.js";
import { createCoreWrapper } from "../fixtures/core-wrapper.js";
import { createCanonicalTemporaryRoot } from "../fixtures/temporary-root.js";

const temporary: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporary
      .splice(0)
      .map((path) => rm(path, { recursive: true, force: true })),
  );
});

describe("Skill management CLI through the real private Core", () => {
  it("installs, lists, and removes a User Skill without initialization or Ink", async () => {
    const root = await createCanonicalTemporaryRoot("awesome-skills-");
    temporary.push(root);
    const home = join(root, "home");
    const workspace = join(root, "workspace");
    const source = join(root, "review-source");
    const wrappers = join(root, "bin");
    await mkdir(workspace);
    await mkdir(source);
    await writeFile(
      join(source, "SKILL.md"),
      [
        "---",
        "name: review",
        "description: Review changes safely",
        "allowed-tools: [read_file]",
        "---",
        "# Review changes",
        "",
        "Inspect the public contract before the implementation.",
        "",
      ].join("\n"),
      "utf8",
    );
    const wrapper = await createCoreWrapper({
      directory: wrappers,
      repository: resolve(".."),
    });
    const executable =
      process.platform === "win32"
        ? join(wrappers, "awesome-core.cmd")
        : "awesome-core";

    const install = await runSkillCli(
      ["skills", "install", source],
      workspace,
      home,
      executable,
      wrapper.environment,
    );
    expect(install).toEqual({
      exitCode: 0,
      stdout: "Installed Skill review. Restart Awesome to use this change.\n",
      stderr: "",
    });

    const list = await runSkillCli(
      ["skills", "list"],
      workspace,
      home,
      executable,
      wrapper.environment,
    );
    expect(list).toEqual({
      exitCode: 0,
      stdout: "Installed User Skills:\n- review: Review changes safely\n",
      stderr: "",
    });

    const remove = await runSkillCli(
      ["skills", "remove", "review", "--yes"],
      workspace,
      home,
      executable,
      wrapper.environment,
    );
    expect(remove).toEqual({
      exitCode: 0,
      stdout: "Removed Skill review. Restart Awesome to use this change.\n",
      stderr: "",
    });

    const empty = await runSkillCli(
      ["skills", "list"],
      workspace,
      home,
      executable,
      wrapper.environment,
    );
    expect(empty).toEqual({
      exitCode: 0,
      stdout: "No User Skills are installed.\n",
      stderr: "",
    });
  }, 60_000);
});

async function runSkillCli(
  argv: readonly string[],
  workspace: string,
  home: string,
  executable: string,
  wrapperEnvironment: Readonly<Record<string, string>>,
): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  const stdout: string[] = [];
  const stderr: string[] = [];
  const dependencies: CliDependencies = {
    argv,
    cwd: () => workspace,
    env: {
      ...wrapperEnvironment,
      AWESOME_HOME: home,
      AWESOME_WORKSPACE: workspace,
      PYTHONUNBUFFERED: "1",
      UV_NO_SYNC: "1",
    },
    nodeVersion: "22.23.1",
    stdinIsTTY: false,
    stdoutIsTTY: false,
    stdoutColorDepth: undefined,
    coreExecutable: executable,
    writeStdout: (value) => stdout.push(value),
    writeStderr: (value) => stderr.push(value),
    startSurface: async (options) =>
      await connectSurface({ ...options, executable }),
    startApplication: vi.fn(async () => {
      throw new Error("Skill management must not initialize Application.");
    }),
    renderApplication: vi.fn(async () => {
      throw new Error("Skill management must not render Ink.");
    }),
  };

  const exitCode = await runCli(dependencies);
  expect(dependencies.startApplication).not.toHaveBeenCalled();
  expect(dependencies.renderApplication).not.toHaveBeenCalled();
  return {
    exitCode,
    stdout: stdout.join(""),
    stderr: stderr.join(""),
  };
}
