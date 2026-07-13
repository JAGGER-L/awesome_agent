import { describe, expect, it } from "vitest";

import type { SurfaceState } from "../../src/state/model.js";
import { projectLiveTurn } from "../../src/transcript/live.js";
import {
  mergeTranscriptBlocks,
  TranscriptIdentityError,
} from "../../src/transcript/merge.js";
import type { TranscriptBlock } from "../../src/transcript/model.js";

function assistantToolAssistant(lastText = "done"): SurfaceState {
  return {
    connection: "ready",
    thread_generation: 0,
    event_sequence: 1,
    warnings: [],
    active_operation: {
      id: "operation_1",
      status: "active",
      turn: {
        id: "turn_1",
        status: "active",
        started_at: "2026-07-13T00:00:00Z",
        thinking_sequence: 0,
        timeline: [
          { kind: "assistant", id: "assistant:turn_1:1", text: "checking" },
          {
            kind: "tool",
            call_id: "call_1",
            tool_name: "read_file",
            status: "completed",
            verb: "Read",
            outcome: "Read",
            summary: "Read file",
          },
          { kind: "assistant", id: "assistant:turn_1:2", text: lastText },
        ],
      },
    },
  };
}

describe("transcript identity invariants", () => {
  it("keeps assistant-tool-assistant keys unique and stable across updates", () => {
    const first = projectLiveTurn(assistantToolAssistant());
    const next = projectLiveTurn(assistantToolAssistant("done now"));
    const keys = first.blocks.map((block) => block.key);

    expect(new Set(keys).size).toBe(keys.length);
    expect(next.blocks.map((block) => block.key)).toEqual(keys);
  });

  it("rejects different semantic blocks that reuse one key", () => {
    const first: TranscriptBlock = {
      key: "assistant:turn_1:1",
      kind: "assistant",
      text: "first",
    };
    const collision: TranscriptBlock = { ...first, text: "second" };

    expect(() => mergeTranscriptBlocks([first], [collision])).toThrow(
      TranscriptIdentityError,
    );
  });

  it("deduplicates the same semantic block", () => {
    const block: TranscriptBlock = {
      key: "command:command_1",
      kind: "command_input",
      submission_id: "command_1",
      text: "/status",
    };

    expect(mergeTranscriptBlocks([block], [{ ...block }])).toEqual([block]);
  });
});
