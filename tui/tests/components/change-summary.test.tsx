import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { ChangeSummary } from "../../src/components/transcript/ChangeSummary.js";

const textChange = {
  kind: "text_file" as const,
  path: "src/main.py",
  change_kind: "updated" as const,
  additions: 16,
  deletions: 2,
};

const changes = [
  textChange,
  {
    kind: "binary_file" as const,
    path: "assets/logo.bin",
    change_kind: "updated" as const,
    before_bytes: 12,
    after_bytes: 20,
  },
  {
    kind: "directory" as const,
    path: "generated",
    change_kind: "created" as const,
  },
  {
    kind: "symlink" as const,
    path: "current",
    change_kind: "updated" as const,
  },
];

describe("ChangeSummary", () => {
  it("folds a text change with the accepted summary", () => {
    const frame = render(
      <ChangeSummary changes={[textChange]} expanded={false} width={80} />,
    ).lastFrame();

    expect(frame).toContain("◇ 1 file changed · Ctrl+O to expand");
    expect(frame).not.toContain("src/main.py");
  });

  it("expands aligned rows with accessible git-style signs", () => {
    const frame = render(
      <ChangeSummary changes={changes} expanded width={80} />,
    ).lastFrame();

    expect(frame).toContain("src/main.py");
    expect(frame).toContain("+16");
    expect(frame).toContain("-2");
    expect(frame).toContain("Binary 12 → 20 bytes");
    expect(frame).toContain("Directory created");
    expect(frame).toContain("Symlink updated");
    expect(
      frame?.split("\n").filter((line) => line.includes("src/main.py")),
    ).toHaveLength(1);
  });

  it("keeps addition and deletion signs in plain terminal text", () => {
    const frame = render(
      <ChangeSummary changes={[textChange]} expanded width={60} />,
    ).lastFrame();

    expect(frame).toMatch(/\+16\s+-2/u);
  });
});
