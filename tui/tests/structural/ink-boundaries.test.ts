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

describe("Ink terminal ownership boundaries", () => {
  it("keeps one keyboard owner and one ordered natural-flow layout", async () => {
    const files = await sourceFiles(
      fileURLToPath(new URL("../../src", import.meta.url)),
    );
    expect(
      files
        .filter((file) => file.source.includes("useInput"))
        .map((file) => file.path),
    ).toEqual(["interaction/TerminalInput.tsx"]);

    const layout =
      files.find((file) => file.path === "components/TerminalSurfaceLayout.tsx")
        ?.source ?? "";
    expect(
      [...layout.matchAll(/from ["']([^"']+)["']/gu)].map((match) => match[1]),
    ).toEqual(["ink", "react", "./cursor/terminal-frame-metrics.js"]);
    const orderedNodes = [
      "props.welcome",
      "props.transcript",
      "props.activeTurn",
      "props.pendingInputs",
      "props.notices",
      "props.commandMenu",
      "props.input",
      "props.status",
    ];
    for (let index = 1; index < orderedNodes.length; index += 1) {
      expect(layout.indexOf(orderedNodes[index - 1] ?? "")).toBeLessThan(
        layout.indexOf(orderedNodes[index] ?? ""),
      );
    }
    expect(layout).toContain("<TerminalFrameMetricsProvider");
    expect(layout).toContain('<Box ref={frameRef} flexDirection="column">');
  });

  it("keeps pending input session-local and outside protocol and Surface state", async () => {
    const files = await sourceFiles(
      fileURLToPath(new URL("../../src", import.meta.url)),
    );
    const pendingFiles = files.filter((file) =>
      file.path.startsWith("pending-input/"),
    );

    expect(pendingFiles.map((file) => file.path).toSorted()).toEqual([
      "pending-input/model.ts",
      "pending-input/reducer.ts",
      "pending-input/use-pending-input-queue.ts",
    ]);
    for (const file of pendingFiles) {
      expect(file.source).not.toContain("../protocol/");
      expect(file.source).not.toContain("../state/");
      expect(file.source).not.toContain("SQLite");
    }
  });

  it("keeps startup and submission coordination local to the Ink session", async () => {
    const files = await sourceFiles(
      fileURLToPath(new URL("../../src", import.meta.url)),
    );
    const app = files.find((file) => file.path === "app/App.tsx")?.source ?? "";
    const main =
      files.find((file) => file.path === "cli/main.tsx")?.source ?? "";
    const submission =
      files.find((file) => file.path === "app/submission-coordinator.ts")
        ?.source ?? "";
    const startup =
      files.find((file) => file.path === "cli/startup-session-controller.ts")
        ?.source ?? "";

    expect(app).toContain("new SubmissionCoordinator");
    expect(main).toContain("new StartupSessionController");
    for (const controller of [submission, startup]) {
      expect(controller).not.toMatch(
        /\b(?:useInput|useReducer|useState|useSyncExternalStore)\b/u,
      );
    }
    expect(submission).not.toContain("usePendingInputQueue");
    expect(startup).not.toContain("BootstrapPhase");
    expect(startup).not.toContain('request("initialize"');
  });

  it("keeps the Ink frame effect in the CLI host", async () => {
    const files = await sourceFiles(
      fileURLToPath(new URL("../../src", import.meta.url)),
    );
    expect(
      files
        .filter((file) => file.source.includes("instance?.clear()"))
        .map((file) => file.path),
    ).toEqual(["cli/main.tsx"]);

    const transition =
      files.find((file) => file.path === "app/use-thread-transition.ts")
        ?.source ?? "";
    expect(transition.indexOf('type: "thread.replaced"')).toBeLessThan(
      transition.indexOf("effects.resetCurrentFrame()"),
    );
  });

  it("replaces one Thread surface with current product projections", async () => {
    const files = await sourceFiles(
      fileURLToPath(new URL("../../src", import.meta.url)),
    );
    const actions =
      files.find((file) => file.path === "state/actions.ts")?.source ?? "";
    const replacement = actions.slice(
      actions.indexOf('readonly type: "thread.replaced"'),
      actions.indexOf('readonly type: "event.received"'),
    );
    expect(replacement).toContain(
      'readonly application: MethodValue["application.getState"]',
    );
    expect(replacement).toContain(
      'readonly thread: MethodValue["thread.read"]',
    );
    expect(replacement).toContain(
      "readonly transcript: readonly TranscriptBlock[]",
    );
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
