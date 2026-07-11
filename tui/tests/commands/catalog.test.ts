import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

import { COMMAND_CATALOG } from "../../src/commands/catalog.js";

describe("COMMAND_CATALOG", () => {
  it("matches the shared command fixture exactly", async () => {
    const fixture = JSON.parse(
      await readFile(
        new URL("../../../protocol/fixtures/v1/commands.json", import.meta.url),
        "utf8",
      ),
    ) as { commands: { name: string; owner: string }[] };
    expect(COMMAND_CATALOG.map(({ name, owner }) => ({ name, owner }))).toEqual(
      fixture.commands,
    );
  });

  it("has exact owner counts and useful metadata", () => {
    expect(
      COMMAND_CATALOG.filter(({ owner }) => owner === "application"),
    ).toHaveLength(19);
    expect(
      COMMAND_CATALOG.filter(({ owner }) => owner === "skill"),
    ).toHaveLength(5);
    expect(COMMAND_CATALOG.filter(({ owner }) => owner === "ink")).toHaveLength(
      4,
    );
    expect(new Set(COMMAND_CATALOG.map(({ name }) => name)).size).toBe(28);
    for (const command of COMMAND_CATALOG) {
      expect(command.description.length).toBeGreaterThan(0);
      expect(command.usage).toMatch(new RegExp(`^/${command.name}(?: |$)`));
    }
  });

  it.each([
    "editor",
    "details",
    "history",
    "clear",
  ])("does not restore /%s", (name) => {
    expect(COMMAND_CATALOG.some((command) => command.name === name)).toBe(
      false,
    );
  });
});
