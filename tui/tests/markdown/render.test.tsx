import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { MarkdownBlock } from "../../src/markdown/MarkdownBlock.js";

describe("MarkdownBlock", () => {
  it.each([
    24, 80,
  ])("renders terminal semantics without source markers at width %i", (width) => {
    const frame =
      render(
        <MarkdownBlock
          source={"# Heading\n\n**bold** and *emphasis*\n\n- item"}
          width={width}
        />,
      ).lastFrame() ?? "";

    expect(frame).toContain("Heading");
    expect(frame).toContain("bold and emphasis");
    expect(frame).toContain("• item");
    expect(frame).not.toContain("# Heading");
    expect(frame).not.toContain("**");
    expect(frame).not.toContain("*emphasis*");
  });

  it("shows raw HTML and link destinations as inert terminal text", () => {
    const frame =
      render(
        <MarkdownBlock
          source={"<script>alert('x')</script>\n\n[docs](https://example.com)"}
          width={80}
        />,
      ).lastFrame() ?? "";

    expect(frame).toContain("<script>");
    expect(frame).toContain("alert('x')");
    expect(frame).toContain("docs (https://example.com)");
  });
});
