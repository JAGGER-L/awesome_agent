import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

import { PRODUCT_VERSION } from "../../src/version.js";

describe("package identity", () => {
  it("uses the product version exported to the protocol", async () => {
    const packageJson = JSON.parse(
      await readFile(new URL("../../package.json", import.meta.url), "utf8"),
    ) as { version: string };
    const packageLock = JSON.parse(
      await readFile(
        new URL("../../package-lock.json", import.meta.url),
        "utf8",
      ),
    ) as { version: string; packages: Record<string, { version?: string }> };
    const repositoryVersion = await readFile(
      new URL("../../../VERSION", import.meta.url),
      "utf8",
    );

    expect(repositoryVersion).toBe("1.1.1\n");
    expect(PRODUCT_VERSION).toBe("1.1.1");
    expect(packageJson.version).toBe(PRODUCT_VERSION);
    expect(packageLock.version).toBe(PRODUCT_VERSION);
    expect(packageLock.packages[""]?.version).toBe(PRODUCT_VERSION);
  });

  it("has no generated version drift", () => {
    const result = spawnSync(
      process.execPath,
      ["scripts/sync-version.mjs", "--check"],
      { cwd: new URL("../..", import.meta.url), encoding: "utf8" },
    );

    expect(result.status, result.stderr || result.stdout).toBe(0);
  });
});
