import { describe, expect, it } from "vitest";

import type { SurfaceState } from "../../src/state/index.js";
import { projectLiveTurn } from "../../src/transcript/live.js";

function state(): SurfaceState {
  return {
    connection: "ready",
    thread_generation: 0,
    event_sequence: 8,
    warnings: [{ code: "retry", message: "Provider retrying." }],
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
        assistant_text: "Working",
        reasoning_text: "private live thought",
        reasoning_seen: true,
        tool_order: ["call_1", "call_2"],
        tools: {
          call_1: {
            call_id: "call_1",
            tool_name: "read_file",
            status: "completed",
            summary: "Read config",
          },
          call_2: {
            call_id: "call_2",
            tool_name: "execute",
            status: "failed",
            summary: "Tests failed",
            error_code: "exit_1",
          },
        },
      },
    },
  };
}

describe("projectLiveTurn", () => {
  it("projects Balanced safe summaries with stable tool order", () => {
    const live = projectLiveTurn(state());
    expect(live.blocks.map((block) => block.kind)).toEqual([
      "assistant",
      "tools",
      "change",
      "warning",
    ]);
    expect(live.blocks[1]).toMatchObject({
      items: [
        { name: "read_file", summary: "Read config" },
        { name: "execute", outcome: "error", error_code: "exit_1" },
      ],
    });
    expect(live.reasoning_text).toBe("private live thought");
    expect(live.usage).toEqual({ input_tokens: 12, output_tokens: 4 });
  });

  it("replaces tool updates by call ID without duplicating order", () => {
    const value = state();
    const operation = value.active_operation;
    if (!operation?.turn) throw new Error("fixture requires an active Turn");
    const live = projectLiveTurn({
      ...value,
      active_operation: {
        ...operation,
        turn: {
          ...operation.turn,
          tool_order: ["call_1", "call_1"],
        },
      },
    });
    expect(live.blocks[1]).toMatchObject({ items: [{ call_id: "call_1" }] });
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
});
