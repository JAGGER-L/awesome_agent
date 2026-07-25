import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { performance } from "node:perf_hooks";

import { afterEach, describe, expect, it } from "vitest";

const temporary: string[] = [];
const packageRoot = resolve(".");
const BUILD_TIMEOUT_MS = 30_000;
const PACK_TIMEOUT_MS = 15_000;
const INSTALL_TIMEOUT_MS = 60_000;
const CLI_TIMEOUT_MS = 15_000;
// Vitest cannot interrupt spawnSync, so its budget must exceed the hard limits below.
const TEST_TIMEOUT_MS =
  BUILD_TIMEOUT_MS +
  PACK_TIMEOUT_MS +
  INSTALL_TIMEOUT_MS +
  CLI_TIMEOUT_MS +
  10_000;

afterEach(async () => {
  await Promise.all(
    temporary
      .splice(0)
      .map((path) => rm(path, { recursive: true, force: true })),
  );
});

describe("awesome package", () => {
  it(
    "packs, installs, and runs from an isolated prefix",
    async () => {
      const root = await mkdtemp(join(tmpdir(), "awesome-package-"));
      temporary.push(root);
      const tarballs = join(root, "tarballs");
      const prefix = join(root, "prefix");
      await mkdir(tarballs);

      npm("build package", ["run", "build"], packageRoot, BUILD_TIMEOUT_MS);
      const packed = npm(
        "pack package",
        ["pack", "--json", "--pack-destination", tarballs],
        packageRoot,
        PACK_TIMEOUT_MS,
      );
      const report = JSON.parse(packed) as Array<{
        filename: string;
        files: Array<{ path: string }>;
      }>;
      expect(report).toHaveLength(1);
      const entry = report[0];
      if (!entry) throw new Error("npm pack did not report a tarball");
      const files = entry.files.map(({ path }) => path).toSorted();

      expect(files).toContain("package.json");
      expect(files).toContain("README.md");
      expect(files).toContain("LICENSE");
      expect(files).toContain("dist/cli/index.js");
      expect(files.some((path) => path.startsWith("src/"))).toBe(false);
      expect(files.some((path) => path.startsWith("tests/"))).toBe(false);
      expect(files.some((path) => path.includes("node_modules"))).toBe(false);
      expect(files.some((path) => path.includes(".codex"))).toBe(false);
      expect(files.some((path) => path.endsWith(".map"))).toBe(false);
      expect(
        files.every((path) =>
          /^(?:dist\/|package\.json$|README\.md$|LICENSE$)/u.test(path),
        ),
      ).toBe(true);

      const tarball = join(tarballs, entry.filename);
      npm(
        "install package",
        [
          "install",
          "--prefix",
          prefix,
          "--ignore-scripts",
          "--no-audit",
          "--no-fund",
          "--prefer-offline",
          tarball,
        ],
        root,
        INSTALL_TIMEOUT_MS,
      );
      const installedPackage = JSON.parse(
        await readFile(
          join(prefix, "node_modules", "@awesome-agent", "tui", "package.json"),
          "utf8",
        ),
      ) as {
        version: string;
        type: string;
        bin: Record<string, string>;
        license: string;
      };
      expect(installedPackage).toMatchObject({
        version: "1.2.1",
        type: "module",
        bin: { awesome: "dist/cli/index.js" },
        license: "UNLICENSED",
      });

      const bin =
        process.platform === "win32"
          ? join(prefix, "node_modules", ".bin", "awesome.cmd")
          : join(prefix, "node_modules", ".bin", "awesome");
      const version =
        process.platform === "win32"
          ? command(
              "run installed CLI",
              process.env.ComSpec ?? "cmd.exe",
              ["/d", "/s", "/c", `call "${bin}" --version`],
              {
                cwd: root,
                windowsVerbatimArguments: true,
                timeoutMs: CLI_TIMEOUT_MS,
              },
            )
          : command("run installed CLI", bin, ["--version"], {
              cwd: root,
              timeoutMs: CLI_TIMEOUT_MS,
            });
      expect(version.status, version.stderr).toBe(0);
      expect(version.stdout).toBe("1.2.1\n");
    },
    TEST_TIMEOUT_MS,
  );

  it("bounds a stalled packaging subprocess", () => {
    const startedAt = performance.now();
    expect(() =>
      command(
        "bounded packaging probe",
        process.execPath,
        ["--eval", "setInterval(() => {}, 1_000)"],
        { cwd: packageRoot, timeoutMs: 100 },
      ),
    ).toThrow(/bounded packaging probe failed after \d+ms/u);
    expect(performance.now() - startedAt).toBeLessThan(5_000);
  }, 10_000);
});

function npm(
  stage: string,
  arguments_: readonly string[],
  cwd: string,
  timeoutMs: number,
): string {
  const npmCli = process.env.npm_execpath;
  const result = npmCli
    ? command(stage, process.execPath, [npmCli, ...arguments_], {
        cwd,
        timeoutMs,
      })
    : command(stage, "npm", arguments_, { cwd, timeoutMs });
  return result.stdout;
}

function command(
  stage: string,
  executable: string,
  arguments_: readonly string[],
  {
    cwd,
    timeoutMs,
    windowsVerbatimArguments = false,
  }: {
    readonly cwd: string;
    readonly timeoutMs: number;
    readonly windowsVerbatimArguments?: boolean;
  },
) {
  const startedAt = performance.now();
  const result = spawnSync(executable, [...arguments_], {
    cwd,
    encoding: "utf8",
    env: { ...process.env },
    timeout: timeoutMs,
    killSignal: "SIGKILL",
    windowsVerbatimArguments,
  });
  const elapsedMs = Math.ceil(performance.now() - startedAt);
  if (result.status !== 0) {
    const diagnostics =
      [result.error?.message, result.stderr, result.stdout]
        .filter((value) => value)
        .join("\n") || "no process diagnostics";
    throw new Error(`${stage} failed after ${elapsedMs}ms\n${diagnostics}`);
  }
  return result;
}
