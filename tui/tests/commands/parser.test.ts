import { describe, expect, it } from "vitest";

import { parseInput } from "../../src/commands/parser.js";

describe("parseInput", () => {
  it("preserves ordinary input including @path and surrounding whitespace", () => {
    expect(parseInput("  inspect @src/app.ts  ")).toEqual({
      kind: "turn",
      content: "  inspect @src/app.ts  ",
    });
  });

  it("preserves direct command text after the classification marker", () => {
    expect(parseInput("  !printf  'a b'  ")).toEqual({
      kind: "direct",
      command: "printf  'a b'  ",
    });
  });

  it("parses quoted and escaped slash arguments without host-shell rules", () => {
    expect(
      parseInput("/skills \"debug session\" one\\ two 'three four'"),
    ).toEqual({
      kind: "command",
      intent: {
        name: "skills",
        arguments: ["debug session", "one two", "three four"],
      },
    });
  });

  it("routes Ink-owned commands locally", () => {
    expect(parseInput("/theme dark")).toEqual({
      kind: "local",
      intent: { name: "theme", arguments: ["dark"] },
    });
  });

  it("returns no-op for empty input", () => {
    expect(parseInput(" \n\t ")).toBeUndefined();
  });

  it.each([
    "/unknown",
    "/editor",
    "/details",
    "/skill",
    "/review",
    "/debug",
    "/test",
    "/commit",
    "/workplace",
  ])("rejects absent command %s", (input) => {
    expect(parseInput(input)).toEqual({
      kind: "invalid",
      code: "unknown_command",
    });
  });

  it.each([
    '/model "unterminated',
    "/model trailing\\",
  ])("rejects malformed arguments in %s", (input) => {
    expect(parseInput(input)).toEqual({
      kind: "invalid",
      code: "invalid_arguments",
    });
  });
});
