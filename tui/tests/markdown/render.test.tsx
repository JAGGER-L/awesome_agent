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

  it.each([
    24, 80,
  ])("aligns mixed CJK tables and preserves formulas at width %i", (width) => {
    const frame =
      render(
        <MarkdownBlock
          source={
            "| Name | Formula |\n| --- | --- |\n| 圆 | S = πr² |\n| square | $A = a^2$ |\n\n$$\n\\sum_{i=1}^n i\n$$"
          }
          width={width}
        />,
      ).lastFrame() ?? "";
    expect(frame).toContain(width < 30 ? "Name: 圆" : "| Name");
    expect(frame).toContain("S = πr²");
    expect(frame).toContain("A = a^2");
    expect(frame).toContain("\\sum_{i=1}^n i");
  });

  it("renders tables and formulas after streaming chunks complete", () => {
    const view = render(<MarkdownBlock source="| Name" width={80} />);
    view.rerender(
      <MarkdownBlock
        source={
          "| Name | Formula |\n| --- | --- |\n| circle | S = πr² |\n\n$E = mc^2$"
        }
        width={80}
      />,
    );
    const frame = view.lastFrame() ?? "";
    expect(frame).toContain("| Name");
    expect(frame).toContain("S = πr²");
    expect(frame).toContain("E = mc^2");
  });
});
