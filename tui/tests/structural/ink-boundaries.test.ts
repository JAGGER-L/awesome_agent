import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

async function sourceFiles(directory: string): Promise<string[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  const values: string[] = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) values.push(...(await sourceFiles(path)));
    else if (/\.tsx?$/.test(entry.name))
      values.push(await readFile(path, "utf8"));
  }
  return values;
}

describe("Ink scrollback boundaries", () => {
  it("contains no alternate screen, mouse, sidebar, or viewport authority", async () => {
    const source = (
      await sourceFiles(fileURLToPath(new URL("../../src", import.meta.url)))
    ).join("\n");
    expect(source).not.toMatch(
      /1049|alternate.?screen|mouse|sidebar|viewport|useInput/i,
    );
  });
});
