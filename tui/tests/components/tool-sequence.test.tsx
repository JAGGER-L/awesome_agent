import { render } from "ink-testing-library";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToolSequence } from "../../src/components/transcript/ToolSequence.js";
import { formatActivityDuration } from "../../src/components/activity/format-duration.js";

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
  afterEach(() => {
    vi.useRealTimers();
  });

  it("folds the whole sequence to exactly one row", () => {
    const frame = render(
      <ToolSequence items={items} width={80} expanded={false} />,
    ).lastFrame();
    expect(frame?.split("\n")).toHaveLength(1);
    expect(frame).toContain("2 tool calls · <0.1 s · Ctrl+O to expand");
    expect(frame).not.toContain("List src");
  });

  it("shows bounded details and the exact omitted-entry count", () => {
    const frame = render(
      <ToolSequence items={items} width={80} expanded />,
    ).lastFrame();
    expect(frame).toContain("List src");
    expect(frame).toContain("Listed · 2 entries · <0.1 s");
    expect(frame).toContain("directory  src/awesome_agent");
    expect(frame).toContain("file       src/main.py");
    expect(frame).toContain("… +3 entries");
    expect(frame).toContain("Read src/main.py");
  });

  it("shows the running tool before replacing local time with terminal duration", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-14T00:00:00.400Z"));
    const view = render(
      <ToolSequence
        items={[
          {
            call_id: "call_running",
            name: "read_file",
            verb: "Read",
            target: "pyproject.toml",
            outcome: "running",
            summary: "Running",
            started_at: "2026-07-14T00:00:00.000Z",
          },
        ]}
        width={80}
        expanded={false}
        activityShimmer
      />,
    );

    expect(view.lastFrame()).toContain(
      "✦ Read pyproject.toml · Running for 0.4 s",
    );
    view.rerender(
      <ToolSequence
        items={[
          {
            call_id: "call_running",
            name: "read_file",
            verb: "Read",
            target: "pyproject.toml",
            outcome: "success",
            summary: "12 lines",
            duration_ms: 812,
            started_at: "2026-07-14T00:00:00.000Z",
          },
        ]}
        width={80}
        expanded={false}
      />,
    );

    expect(view.lastFrame()).toContain(
      "1 tool call · 0.8 s · Ctrl+O to expand",
    );
    expect(view.lastFrame()).not.toContain("Running for");
  });

  it.each([
    [16, "<0.1 s"],
    [149, "0.1 s"],
    [812, "0.8 s"],
    [1_250, "1.3 s"],
  ])("formats %i milliseconds as %s", (durationMs, expected) => {
    expect(formatActivityDuration(durationMs)).toBe(expected);
  });
});
