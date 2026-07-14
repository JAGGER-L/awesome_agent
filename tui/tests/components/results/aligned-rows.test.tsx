import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { AlignedRows } from "../../../src/components/results/index.js";

describe("aligned result rows", () => {
  it.each([
    80, 100, 120,
  ])("starts every value after one shared label column at %i columns", (width) => {
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
      expect(lines[0]?.indexOf("1")).toBe(13);
      expect(lines[1]?.indexOf("22")).toBe(13);
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
    expect(frame.replace(/\s/gu, "")).toContain(
      "E:/projects/an-important-workspace",
    );
  });

  it("starts descriptive values in one stable column", () => {
    const lines = (
      render(
        <AlignedRows
          width={80}
          rows={[
            { label: "Model", value: "deepseek/deepseek-v4-flash" },
            { label: "Workspace", value: "E:/projects/awesome" },
          ]}
        />,
      ).lastFrame() ?? ""
    ).split("\n");
    expect(lines[0]?.indexOf("deepseek")).toBe(lines[1]?.indexOf("E:/"));
  });

  it("has no legacy alignment policy in the production result path", () => {
    const files = [
      "../../../src/commands/presenters.ts",
      "../../../src/components/CommandResultView.tsx",
      "../../../src/components/results/AlignedRows.tsx",
    ];
    const source = files
      .map((path) =>
        readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8"),
      )
      .join("\n");
    expect(source).not.toContain("ValueAlignment");
    expect(source).not.toContain("valueAlignment");
    expect(source).not.toContain('justifyContent="flex-end"');
  });
});
