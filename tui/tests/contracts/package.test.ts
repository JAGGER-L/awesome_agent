import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

import { PRODUCT_VERSION } from "../../src/version.js";

describe("package identity", () => {
  it("uses the product version exported to the protocol", async () => {
    const packageJson = JSON.parse(
      await readFile(new URL("../../package.json", import.meta.url), "utf8"),
    ) as { version: string };

    expect(PRODUCT_VERSION).toBe(packageJson.version);
  });
});
