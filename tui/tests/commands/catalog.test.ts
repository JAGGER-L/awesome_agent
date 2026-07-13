import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

import { COMMAND_CATALOG } from "../../src/commands/catalog.js";

describe("COMMAND_CATALOG", () => {
  it("matches the shared command fixture exactly", async () => {
    const fixture = JSON.parse(
      await readFile(
        new URL("../../../protocol/fixtures/v2/commands.json", import.meta.url),
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
    ).toHaveLength(20);
    expect(
      COMMAND_CATALOG.filter(({ owner }) => owner === "skill"),
    ).toHaveLength(1);
    expect(COMMAND_CATALOG.filter(({ owner }) => owner === "ink")).toHaveLength(
      4,
    );
    expect(new Set(COMMAND_CATALOG.map(({ name }) => name)).size).toBe(25);
    for (const command of COMMAND_CATALOG) {
      expect(command.completion).toBe(`/${command.name}`);
      expect(command.completion).not.toContain("[");
      expect(command.completion).not.toContain("]");
      expect(command.description.length).toBeGreaterThan(0);
      expect(command.usage).toMatch(new RegExp(`^/${command.name}(?: |$)`));
    }
    expect(
      COMMAND_CATALOG.find(({ name }) => name === "workspace")?.description,
    ).toBe("Show the current workspace path");
  });

  it.each([
    "editor",
    "details",
    "history",
    "clear",
    "exit",
    "skill",
    "review",
    "debug",
    "test",
    "commit",
    "workplace",
  ])("does not restore /%s", (name) => {
    expect(COMMAND_CATALOG.some((command) => command.name === name)).toBe(
      false,
    );
  });
});
