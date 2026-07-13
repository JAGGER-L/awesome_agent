import { describe, expect, it } from "vitest";

import type { SurfaceState } from "../../src/state/index.js";
import { projectLiveTurn } from "../../src/transcript/live.js";
import { stableStreamingSource } from "../../src/markdown/streaming.js";

function state(): SurfaceState {
  return {
    connection: "ready",
    thread_generation: 0,
    event_sequence: 8,
    warnings: [
      {
        id: "warning:retry:1",
        code: "retry",
        message: "Provider retrying.",
        count: 1,
      },
    ],
    usage: { input_tokens: 12, output_tokens: 4 },
    latest_change: {
      change_set_id: "change_1",
      paths: ["src/a.py"],
      reversibility: "full",
    },
    active_operation: {
      id: "operation_1",
      status: "active",
      turn: {
        id: "turn_1",
        status: "active",
        started_at: "2026-07-11T08:00:00Z",
        thinking_sequence: 2,
        timeline: [
          {
            kind: "thinking",
            id: "thinking:0",
            started_at: "2026-07-11T08:00:00Z",
            text: "first thought",
            duration_ms: 2000,
          },
          {
            kind: "tool",
            call_id: "call_1",
            tool_name: "read_file",
            status: "completed",
            started_at: "2026-07-11T08:00:01Z",
            verb: "Read",
            target: "config.py",
            outcome: "Read",
            summary: "Read config",
          },
          {
            kind: "thinking",
            id: "thinking:1",
            started_at: "2026-07-11T08:00:02Z",
            text: "second thought",
            duration_ms: 1000,
          },
          {
            kind: "tool",
            call_id: "call_2",
            tool_name: "execute",
            status: "failed",
            started_at: "2026-07-11T08:00:03Z",
            verb: "Run",
            target: "pytest",
            outcome: "Failed",
            summary: "Tests failed",
            error_code: "exit_1",
          },
          {
            kind: "assistant",
            id: "assistant:turn_1",
            text: "Working",
          },
        ],
      },
    },
  };
}

describe("projectLiveTurn", () => {
  it("keeps streaming source intact for the shared parser", () => {
    expect(stableStreamingSource("# Heading\n\n- item\n\n**partial")).toBe(
      "# Heading\n\n- item\n\n**partial",
    );
  });

  it("projects Balanced safe summaries with stable tool order", () => {
    const live = projectLiveTurn(state());
    expect(live.blocks.map((block) => block.kind)).toEqual([
      "thinking",
      "tools",
      "thinking",
      "assistant",
      "change",
      "warning",
    ]);
    expect(live.blocks[1]).toMatchObject({
      items: [
        { verb: "Read", target: "config.py", summary: "Read config" },
        { verb: "Run", outcome: "error", error_code: "exit_1" },
      ],
    });
    expect(live.blocks[0]).toMatchObject({
      kind: "thinking",
      text: "first thought",
      duration_ms: 2000,
    });
    expect(live.usage).toEqual({ input_tokens: 12, output_tokens: 4 });
  });

  it("keeps reasoning inside one assistant-bounded tool sequence", () => {
    const value = state();
    const operation = value.active_operation;
    if (!operation?.turn) throw new Error("fixture requires an active Turn");
    const live = projectLiveTurn({
      ...value,
      active_operation: {
        ...operation,
        turn: {
          ...operation.turn,
          timeline: operation.turn.timeline.filter(
            (item) => item.kind !== "assistant",
          ),
        },
      },
    });
    expect(live.blocks.filter((block) => block.kind === "tools")).toHaveLength(
      1,
    );
  });

  it("starts a new tool sequence after an assistant segment", () => {
    const value = state();
    const operation = value.active_operation;
    if (!operation?.turn) throw new Error("fixture requires an active Turn");
    const [firstThinking, firstTool, secondThinking, secondTool] =
      operation.turn.timeline;
    if (!firstThinking || !firstTool || !secondThinking || !secondTool) {
      throw new Error("fixture requires four timeline items");
    }
    const live = projectLiveTurn({
      ...value,
      active_operation: {
        ...operation,
        turn: {
          ...operation.turn,
          timeline: [
            firstThinking,
            firstTool,
            { kind: "assistant", id: "assistant:between", text: "interim" },
            secondThinking,
            secondTool,
          ],
        },
      },
    });

    expect(live.blocks.filter((block) => block.kind === "tools")).toHaveLength(
      2,
    );
  });

  it("marks only terminal operations terminal", () => {
    const value = state();
    const operation = value.active_operation;
    if (!operation) throw new Error("fixture requires an active Operation");
    expect(projectLiveTurn(value).terminal).toBe(false);
    expect(
      projectLiveTurn({
        ...value,
        active_operation: { ...operation, status: "completed" },
      }).terminal,
    ).toBe(true);
  });

  it("does not expose usage without a live Turn", () => {
    const value = state();

    const live = projectLiveTurn({
      ...value,
      active_operation: { id: "operation_1", status: "completed" },
    });

    expect(live.usage).toBeUndefined();
  });

  it("does not present Thought timing when no reasoning interval exists", () => {
    const value = state();
    const operation = value.active_operation;
    if (!operation?.turn) throw new Error("fixture requires an active Turn");
    const live = projectLiveTurn({
      ...value,
      active_operation: {
        ...operation,
        turn: {
          ...operation.turn,
          timeline: operation.turn.timeline.filter(
            (item) => item.kind !== "thinking",
          ),
        },
      },
    });

    expect(live.blocks.some((block) => block.kind === "thinking")).toBe(false);
  });
});
