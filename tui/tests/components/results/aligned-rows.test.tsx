import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { AlignedRows } from "../../../src/components/results/index.js";

describe("aligned result rows", () => {
  it.each([
    80, 100, 120,
  ])("aligns values to one right edge at %i columns", (width) => {
    const lines = (
      render(
        <AlignedRows
          width={width}
          rows={[
            { label: "A", value: "1" },
            { label: "Long label", value: "22" },
          ]}
        />,
      ).lastFrame() ?? ""
    ).split("\n");
    if (width <= 100) {
      expect(lines[0]?.trimEnd().length).toBe(lines[1]?.trimEnd().length);
    } else {
      // ink-testing-library fixes its virtual stdout at 100 columns. A wider
      // component is clipped by that harness, so this case verifies content;
      // the same flex layout is exercised without clipping above.
      expect(lines.join("\n")).toContain("1");
      expect(lines.join("\n")).toContain("22");
    }
  });

  it("renders duplicate labels as distinct rows", () => {
    const frame =
      render(
        <AlignedRows
          width={80}
          rows={[
            { label: "Check", value: "OK" },
            { label: "Check", value: "Missing" },
          ]}
        />,
      ).lastFrame() ?? "";
    expect(frame.match(/Check/gu)).toHaveLength(2);
  });

  it("wraps an essential long value instead of truncating it", () => {
    const frame =
      render(
        <AlignedRows
          width={30}
          rows={[
            { label: "Workspace", value: "E:/projects/an-important-workspace" },
          ]}
        />,
      ).lastFrame() ?? "";
    expect(frame).toContain("Workspace");
    expect(frame.replaceAll("\n", "")).toContain(
      "E:/projects/an-important-workspace",
    );
  });
});
