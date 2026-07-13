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
    ).toEqual(["interaction/TerminalInput.tsx"]);

    const transcript = files
      .filter((file) => file.path.includes("transcript"))
      .map((file) => file.source)
      .join("\n");
    expect(transcript).not.toMatch(/useInput|viewport/iu);
  });

  it("contains no superseded input, modal, role-label, or timing paths", async () => {
    const files = await sourceFiles(
      fileURLToPath(new URL("../../src", import.meta.url)),
    );
    const source = files.map((file) => file.source).join("\n");
    const app = files.find((file) => file.path === "app/App.tsx")?.source ?? "";

    expect(app).not.toMatch(
      /commandInputBlocked|CredentialFlow|showHelp|helpOverlay|isModalOpen/u,
    );
    expect(source).not.toContain("execute_boundary");
    expect(source).not.toMatch(/choices\??:\s*(?:readonly\s+)?string\[\]/u);
    expect(source).not.toMatch(/<Text[^>]*>\s*(?:You|Assistant)\s*</u);
    expect(source).not.toMatch(/duration_ms\s*:\s*0\b/u);
    expect(files.some((file) => /HelpOverlay/iu.test(file.path))).toBe(false);
  });

  it("keeps Aurora brand colors in the semantic theme boundary", async () => {
    const files = await sourceFiles(
      fileURLToPath(new URL("../../src", import.meta.url)),
    );
    const ownedDirectories = [
      "components/",
      "app/",
      "transcript/",
      "markdown/",
    ];
    const localBrandColor = /#[0-9A-F]{6}|\b(?:greenBright|cyanBright)\b/iu;
    const offenders = files
      .filter((file) =>
        ownedDirectories.some((directory) => file.path.startsWith(directory)),
      )
      .filter((file) => localBrandColor.test(file.source))
      .map((file) => file.path);

    expect(offenders).toEqual([]);
  });

  it("routes major terminal surfaces through stable semantic roles", async () => {
    const files = await sourceFiles(
      fileURLToPath(new URL("../../src", import.meta.url)),
    );
    const source = new Map(files.map((file) => [file.path, file.source]));
    const expectedRoles = new Map<string, readonly string[]>([
      [
        "components/Welcome.tsx",
        ["props.theme.logoRows", "props.theme.border", "props.theme.muted"],
      ],
      [
        "components/Composer.tsx",
        ["theme.border", "theme.primary", "theme.secondary"],
      ],
      ["components/TrustPrompt.tsx", ["theme.brand", "theme.muted"]],
      [
        "components/interactions/SelectionPanel.tsx",
        ["theme.brand", "theme.warning", "theme.danger", "theme.primary"],
      ],
      ["components/SecretInput.tsx", ["theme.primary", "theme.warning"]],
      [
        "components/transcript/blocks/BlockView.tsx",
        ["theme.assistant", "theme.muted"],
      ],
      ["components/transcript/UserLine.tsx", ["theme.user", "theme.danger"]],
      ["components/transcript/ToolSequence.tsx", ["theme.tool", "theme.muted"]],
    ]);

    for (const [path, roles] of expectedRoles) {
      const component = source.get(path) ?? "";
      for (const role of roles) expect(component).toContain(role);
    }
  });
});
