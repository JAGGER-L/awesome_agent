import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

import { afterEach, describe, expect, it } from "vitest";

const temporary: string[] = [];
const packageRoot = resolve(".");

afterEach(async () => {
  await Promise.all(
    temporary
      .splice(0)
      .map((path) => rm(path, { recursive: true, force: true })),
  );
});

describe("awesome package", () => {
  it("packs, installs, and runs from an isolated prefix", async () => {
    const root = await mkdtemp(join(tmpdir(), "awesome-package-"));
    temporary.push(root);
    const tarballs = join(root, "tarballs");
    const prefix = join(root, "prefix");
    await mkdir(tarballs);

    npm(["run", "build"], packageRoot);
    const packed = npm(
      ["pack", "--json", "--pack-destination", tarballs],
      packageRoot,
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
    npm(["install", "--prefix", prefix, "--ignore-scripts", tarball], root);
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
      version: "1.0.0",
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
        ? spawnSync(
            process.env.ComSpec ?? "cmd.exe",
            ["/d", "/s", "/c", `call "${bin}" --version`],
            {
              cwd: root,
              encoding: "utf8",
              windowsVerbatimArguments: true,
              env: { ...process.env },
            },
          )
        : spawnSync(bin, ["--version"], {
            cwd: root,
            encoding: "utf8",
            env: { ...process.env },
          });
    expect(version.status, version.stderr).toBe(0);
    expect(version.stdout).toBe("1.0.0\n");
  }, 60_000);
});

function npm(arguments_: readonly string[], cwd: string): string {
  const npmCli = process.env.npm_execpath;
  const result = npmCli
    ? spawnSync(process.execPath, [npmCli, ...arguments_], {
        cwd,
        encoding: "utf8",
      })
    : spawnSync("npm", [...arguments_], { cwd, encoding: "utf8" });
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || "npm failed");
  }
  return result.stdout;
}
