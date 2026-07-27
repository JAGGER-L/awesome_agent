import { mkdir, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { runCli, type CliDependencies } from "../../src/cli/main.js";
import { connectSurface } from "../../src/surface/controller.js";
import { beginStartup } from "../../src/surface/startup.js";
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

describe("headless CLI through the real private Core", () => {
  it.each([
    "text",
    "json",
  ] as const)("emits pure %s output from durable state", async (format) => {
    const root = await createCanonicalTemporaryRoot("awesome-headless-");
    temporary.push(root);
    const home = join(root, "home");
    const workspace = join(root, "workspace");
    const wrappers = join(root, "bin");
    await mkdir(workspace);
    await writeFile(join(workspace, "sample.txt"), "fixture source", "utf8");
    const wrapper = await createCoreWrapper({
      directory: wrappers,
      repository: resolve(".."),
    });
    const executable =
      process.platform === "win32"
        ? join(wrappers, "awesome-core.cmd")
        : "awesome-core";
    const stdout: string[] = [];
    const stderr: string[] = [];
    const dependencies: CliDependencies = {
      argv: [
        "run",
        "return the fixture answer",
        "--trust-workspace",
        "--format",
        format,
      ],
      cwd: () => workspace,
      env: {
        ...wrapper.environment,
        AWESOME_HOME: home,
        AWESOME_WORKSPACE: workspace,
        AWESOME_FAKE_PROVIDER: "deepseek",
        PYTHONUNBUFFERED: "1",
        UV_NO_SYNC: "1",
      },
      nodeVersion: "22.18.0",
      stdinIsTTY: false,
      stdoutIsTTY: false,
      stdoutColorDepth: undefined,
      coreExecutable: executable,
      writeStdout: (value) => stdout.push(value),
      writeStderr: (value) => stderr.push(value),
      startSurface: async (options) =>
        await connectSurface({ ...options, executable }),
      startApplication: beginStartup,
      renderApplication: vi.fn(async () => {
        throw new Error("Ink must not render for awesome run");
      }),
    };

    await expect(runCli(dependencies)).resolves.toBe(0);

    expect(stderr).toEqual([]);
    expect(dependencies.renderApplication).not.toHaveBeenCalled();
    if (format === "text") {
      expect(stdout.join("")).toBe("fixture done\n");
    } else {
      expect(stdout).toHaveLength(1);
      expect(JSON.parse(stdout[0] ?? "")).toMatchObject({
        version: 2,
        type: "awesome.run.result",
        text: "fixture done",
        citations: [],
      });
    }
  }, 60_000);
});
