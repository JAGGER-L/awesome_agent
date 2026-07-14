import { describe, expect, it } from "vitest";

import { COMMAND_CATALOG } from "../../src/commands/catalog.js";
import { commandMenuWindow } from "../../src/commands/menu-window.js";

describe("commandMenuWindow", () => {
  it("keeps a selected command visible inside a ten-row viewport", () => {
    const selected = COMMAND_CATALOG[12];
    if (!selected) throw new Error("Catalog fixture is incomplete.");

    expect(commandMenuWindow(COMMAND_CATALOG, selected.name, 0)).toMatchObject({
      start: 3,
      end: 13,
      total: 25,
    });
  });

  it("returns an empty, valid window for no matches", () => {
    expect(commandMenuWindow([], undefined, 0)).toEqual({
      items: [],
      start: 0,
      end: 0,
      total: 0,
    });
  });
});
