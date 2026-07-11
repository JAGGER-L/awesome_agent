import { mkdtemp, readFile, readdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  loadPreferences,
  savePreferences,
} from "../../src/preferences/store.js";

const temporaryHome = async () => await mkdtemp(join(tmpdir(), "awesome-ui-"));

describe("loadPreferences", () => {
  it("defaults an absent file to system without warning", async () => {
    await expect(loadPreferences(await temporaryHome())).resolves.toEqual({
      preferences: { schema_version: 1, theme: "system" },
      warnings: [],
    });
  });

  it.each([
    "system",
    "dark",
    "light",
  ] as const)("loads theme %s", async (theme) => {
    const home = await temporaryHome();
    await writeFile(
      join(home, "ui.json"),
      JSON.stringify({ schema_version: 1, theme }),
    );
    await expect(loadPreferences(home)).resolves.toMatchObject({
      preferences: { schema_version: 1, theme },
      warnings: [],
    });
  });

  it.each([
    "{",
    JSON.stringify({ schema_version: 2, theme: "dark" }),
    JSON.stringify({ schema_version: 1, theme: "dark", model: "secret" }),
  ])("falls back once for corrupt content", async (content) => {
    const home = await temporaryHome();
    await writeFile(join(home, "ui.json"), content);
    const result = await loadPreferences(home);
    expect(result.preferences.theme).toBe("system");
    expect(result.warnings).toHaveLength(1);
  });

  it("falls back once for a non-missing read error", async () => {
    const result = await loadPreferences("/ignored", {
      readFile: async () => {
        throw Object.assign(new Error("denied"), { code: "EACCES" });
      },
    });
    expect(result.preferences.theme).toBe("system");
    expect(result.warnings).toEqual([
      { code: "ui_preferences_unreadable", message: "Unable to read ui.json." },
    ]);
  });
});

describe("savePreferences", () => {
  it("creates the parent and atomically persists only schema and theme", async () => {
    const root = await temporaryHome();
    const home = join(root, "nested");
    await savePreferences(home, { schema_version: 1, theme: "dark" });
    expect(JSON.parse(await readFile(join(home, "ui.json"), "utf8"))).toEqual({
      schema_version: 1,
      theme: "dark",
    });
    expect(await readdir(home)).toEqual(["ui.json"]);
  });

  it("rejects unknown fields before writing", async () => {
    await expect(
      savePreferences(await temporaryHome(), {
        schema_version: 1,
        theme: "dark",
        model: "must-not-persist",
      } as never),
    ).rejects.toThrow();
  });

  it("removes its temporary file when replace fails", async () => {
    const removed: string[] = [];
    await expect(
      savePreferences(
        "/home",
        { schema_version: 1, theme: "light" },
        {
          mkdir: async () => undefined,
          writeFile: async () => undefined,
          rename: async () => {
            throw new Error("replace failed");
          },
          rm: async (path) => {
            removed.push(path);
          },
          temporaryName: () => "ui.json.test.tmp",
        },
      ),
    ).rejects.toThrow("replace failed");
    expect(removed).toEqual([join("/home", "ui.json.test.tmp")]);
  });
});
