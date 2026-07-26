import assert from "node:assert/strict";
import test from "node:test";

import {
  markdownExternalUrls,
  markdownInlineCodeLiterals,
  markdownLinks,
  markdownProseText,
  markdownStructure,
  rewriteMarkdownLinks,
} from "./markdown-ast.mjs";

test("discovers multiline, titled, and reference links with one Markdown AST", () => {
  const markdown = `# Links

[Multiline](
  guide.md
  "Guide title"
)

[Reference][guide]

[guide]: reference.md "Reference title"

\`[Code](ignored.md)\`

\`\`\`text
[Fence](ignored.md)
\`\`\`
`;

  assert.deepEqual(
    markdownLinks(markdown).map(({ destination, type }) => ({ destination, type })),
    [
      { destination: "guide.md", type: "link" },
      { destination: "reference.md", type: "definition" },
    ],
  );
});

test("counts each reference-link use and resolves duplicate definitions deterministically", () => {
  const markdown = `# References

[First][shared] and [Second][SHARED].

[shared]: https://example.test/first
[SHARED]: https://example.test/duplicate
[unused]: https://example.test/unused
`;

  assert.deepEqual(markdownLinks(markdown), [
    {
      destination: "https://example.test/first",
      line: 5,
      referenceLine: 3,
      type: "definition",
    },
    {
      destination: "https://example.test/first",
      line: 5,
      referenceLine: 3,
      type: "definition",
    },
  ]);
  assert.deepEqual(markdownExternalUrls(markdown), [
    "https://example.test/first",
    "https://example.test/first",
  ]);
});

test("derives prose, external URLs, and inline code from the same Markdown AST", () => {
  const markdown = `# Guide

Visible prose with [a link](https://example.test/guide) and \
\`operation_busy\`.

\`\`\`text
Hidden code https://ignored.test and \`ignored\`.
\`\`\`

<!-- hidden prose -->
`;

  assert.equal(markdownProseText(markdown), "Guide Visible prose with a link and .");
  assert.deepEqual(markdownExternalUrls(markdown), ["https://example.test/guide"]);
  assert.deepEqual(markdownInlineCodeLiterals(markdown), ["operation_busy"]);
});

test("rewrites every parsed link target without changing code block contents", () => {
  const markdown = `# Links

[Titled](guide.md "Guide") and [Reference][guide].

[guide]: reference.md

\`\`\`python
print("[Code](unchanged.md)")
\`\`\`
`;
  const beforeCode = markdownStructure(markdown).fences;
  const rewritten = rewriteMarkdownLinks(markdown, (destination) =>
    destination.endsWith(".md") ? destination.replace(/\.md$/u, "/") : destination,
  );

  assert.deepEqual(
    markdownLinks(rewritten).map(({ destination }) => destination),
    ["guide/", "reference/"],
  );
  assert.deepEqual(markdownStructure(rewritten).fences, beforeCode);
  assert.match(rewritten, /\[Code\]\(unchanged\.md\)/u);
});

test("rewrites only the first used reference definition", () => {
  const markdown = `[One][shared] and [Two][SHARED].

[shared]: first.md
[SHARED]: duplicate.md
[unused]: unused.md
`;
  const visited = [];
  const rewritten = rewriteMarkdownLinks(markdown, (destination) => {
    visited.push(destination);
    return `${destination}.rewritten`;
  });

  assert.deepEqual(visited, ["first.md"]);
  assert.deepEqual(
    markdownLinks(rewritten).map(({ destination }) => destination),
    ["first.md.rewritten", "first.md.rewritten"],
  );
  assert.match(rewritten, /\[SHARED\]: duplicate\.md/u);
  assert.match(rewritten, /\[unused\]: unused\.md/u);
});
