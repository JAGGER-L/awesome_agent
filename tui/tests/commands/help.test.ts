import { describe, expect, it } from "vitest";

import { helpForCommand, helpOverview } from "../../src/commands/help.js";

describe("catalog-driven help", () => {
  it("returns one semantic row for every command in catalog order", () => {
    const help = helpOverview();
    expect(help.rows).toHaveLength(28);
    expect(help.rows.every((row) => row.usage.startsWith("/"))).toBe(true);
    expect(help.rows.every((row) => row.description.length > 0)).toBe(true);
  });

  it("returns one focused row without internal ownership", () => {
    const help = helpForCommand("thinking");
    expect(help?.rows).toEqual([
      {
        usage: "/thinking [on|off]",
        description: "Show or choose thinking mode",
      },
    ]);
    expect(JSON.stringify(help)).not.toContain("owner");
  });

  it("teaches quoting for multi-word conversation searches", () => {
    expect(helpForCommand("search")?.rows).toEqual([
      {
        usage: "/search <query> [thread_id]",
        description:
          "Search conversations in this workspace; quote multi-word queries",
      },
    ]);
  });
});
