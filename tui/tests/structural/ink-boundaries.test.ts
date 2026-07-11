import { readFile, readdir } from "node:fs/promises";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

type SourceFile = { readonly path: string; readonly source: string };

async function sourceFiles(
  root: string,
  directory = root,
): Promise<SourceFile[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  const values: SourceFile[] = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) values.push(...(await sourceFiles(root, path)));
    else if (/\.tsx?$/u.test(entry.name)) {
      values.push({
        path: relative(root, path).replaceAll("\\", "/"),
        source: await readFile(path, "utf8"),
      });
    }
  }
  return values;
}

describe("Ink scrollback boundaries", () => {
  it("keeps terminal input and viewport authority in focused components", async () => {
    const files = await sourceFiles(
      fileURLToPath(new URL("../../src", import.meta.url)),
    );
    const source = files.map((file) => file.source).join("\n");
    expect(source).not.toMatch(/1049|alternate.?screen|mouse|sidebar/iu);

    expect(
      files
        .filter((file) => file.source.includes("useInput"))
        .map((file) => file.path),
    ).toEqual([
      "components/Composer.tsx",
      "components/Help.tsx",
      "components/Picker.tsx",
    ]);

    const transcript = files
      .filter((file) => file.path.includes("transcript"))
      .map((file) => file.source)
      .join("\n");
    expect(transcript).not.toMatch(/useInput|viewport/iu);
  });
});
