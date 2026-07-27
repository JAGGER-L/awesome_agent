import { describe, expect, it } from "vitest";

import { searchCommands } from "../../src/commands/search.js";

describe("searchCommands", () => {
  it("orders exact, prefix, then description matches", () => {
    expect(searchCommands("model")[0]?.name).toBe("model");
    expect(
      searchCommands("m")
        .slice(0, 3)
        .map(({ name }) => name),
    ).toEqual(["model", "mcp", "memory"]);
    expect(searchCommands("clipboard").map(({ name }) => name)).toEqual([
      "copy",
    ]);
  });

  it("accepts slash-prefixed queries and returns the complete result set", () => {
    expect(searchCommands("/th")[0]?.name).toBe("thinking");
    expect(searchCommands("")).toHaveLength(26);
  });

  it("does not provide fuzzy aliases", () => {
    expect(searchCommands("mdl")).toEqual([]);
    expect(searchCommands("exit")).toEqual([]);
  });
});
