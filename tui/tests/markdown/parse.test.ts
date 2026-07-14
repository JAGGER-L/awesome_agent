import { describe, expect, it } from "vitest";

import { parseTerminalMarkdown } from "../../src/markdown/parse.js";

describe("parseTerminalMarkdown", () => {
  it("maps supported block and inline syntax without producing HTML", () => {
    const nodes = parseTerminalMarkdown(
      [
        "# Heading",
        "",
        "**bold** and *emphasis* with `code` and [link](https://example.com)",
        "",
        "- first",
        "- second",
        "",
        "> quote",
        "",
        "```ts",
        "const value = 1;",
        "```",
      ].join("\n"),
    );

    expect(nodes.map((node) => node.kind)).toEqual([
      "heading",
      "paragraph",
      "list",
      "quote",
      "code",
    ]);
    expect(nodes[1]).toMatchObject({
      kind: "paragraph",
      children: expect.arrayContaining([
        expect.objectContaining({ kind: "strong" }),
        expect.objectContaining({ kind: "emphasis" }),
        expect.objectContaining({ kind: "inline_code" }),
        expect.objectContaining({
          kind: "link",
          href: "https://example.com",
        }),
      ]),
    });
  });

  it("keeps CJK, raw HTML, and incomplete fences as visible text", () => {
    const nodes = parseTerminalMarkdown(
      "中文 <span>不会执行</span>\n\n```python\nprint('ok')",
    );
    expect(JSON.stringify(nodes)).toContain("中文");
    expect(JSON.stringify(nodes)).toContain("<span>");
    expect(nodes.at(-1)).toMatchObject({
      kind: "code",
      language: "python",
      text: "print('ok')",
    });
  });

  it("creates semantic table and math nodes", () => {
    const nodes = parseTerminalMarkdown(
      "| Name | 公式 |\n| --- | ---: |\n| 圆 | $S = πr²$ |\n\n$$\nE = mc^2\n$$",
    );
    expect(nodes.map((node) => node.kind)).toEqual(["table", "math"]);
    expect(nodes[0]).toMatchObject({
      kind: "table",
      align: [null, "right"],
      rows: [
        [
          expect.any(Array),
          [expect.objectContaining({ kind: "math", text: "S = πr²" })],
        ],
      ],
    });
    expect(nodes[1]).toEqual({ kind: "math", text: "E = mc^2" });
  });
});
