import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { ToolSequence } from "../../src/components/transcript/ToolSequence.js";

const items = [
  {
    call_id: "call_list",
    name: "ls",
    verb: "List",
    target: "src",
    outcome: "success" as const,
    presentation_outcome: "Listed",
    summary: "2 entries",
    detail: "directory  src/awesome_agent\nfile       src/main.py",
    detail_truncated_count: 3,
    duration_ms: 16,
  },
  {
    call_id: "call_read",
    name: "read_file",
    verb: "Read",
    target: "src/main.py",
    outcome: "success" as const,
    summary: "12 lines",
    duration_ms: 4,
  },
];

describe("ToolSequence", () => {
  it("folds the whole sequence to exactly one row", () => {
    const frame = render(
      <ToolSequence items={items} width={80} expanded={false} />,
    ).lastFrame();
    expect(frame?.split("\n")).toHaveLength(1);
    expect(frame).toContain("2 tool calls · 20ms · Ctrl+O to expand");
    expect(frame).not.toContain("List src");
  });

  it("shows bounded details and the exact omitted-entry count", () => {
    const frame = render(
      <ToolSequence items={items} width={80} expanded />,
    ).lastFrame();
    expect(frame).toContain("List src");
    expect(frame).toContain("Listed · 2 entries · 16ms");
    expect(frame).toContain("directory  src/awesome_agent");
    expect(frame).toContain("file       src/main.py");
    expect(frame).toContain("… +3 entries");
    expect(frame).toContain("Read src/main.py");
  });
});
