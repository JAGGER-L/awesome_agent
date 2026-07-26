import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

import { PRODUCT_VERSION } from "../../src/version.js";

describe("package identity", () => {
  it("uses the product version exported to the protocol", async () => {
    const packageJson = JSON.parse(
      await readFile(new URL("../../package.json", import.meta.url), "utf8"),
    ) as { version: string; license: string; scripts: Record<string, string> };
    const packageLock = JSON.parse(
      await readFile(
        new URL("../../package-lock.json", import.meta.url),
        "utf8",
      ),
    ) as {
      version: string;
      packages: Record<string, { version?: string; license?: string }>;
    };
    const repositoryVersion = await readFile(
      new URL("../../../VERSION", import.meta.url),
      "utf8",
    );

    expect(repositoryVersion).toBe("1.3.0\n");
    expect(PRODUCT_VERSION).toBe("1.3.0");
    expect(packageJson.version).toBe(PRODUCT_VERSION);
    expect(packageJson.license).toBe("MIT");
    expect(packageJson.scripts.build).toContain("version:check");
    expect(packageJson.scripts.build).not.toContain("version:sync");
    expect(packageLock.version).toBe(PRODUCT_VERSION);
    expect(packageLock.packages[""]?.version).toBe(PRODUCT_VERSION);
    expect(packageLock.packages[""]?.license).toBe("MIT");
    await expect(
      readFile(new URL("../../LICENSE", import.meta.url), "utf8"),
    ).resolves.toBe(
      await readFile(new URL("../../../LICENSE", import.meta.url), "utf8"),
    );
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
