import { describe, expect, it } from "vitest";

import { mergeTranscriptBlocks } from "../../src/transcript/merge.js";
import type { TranscriptBlock } from "../../src/transcript/model.js";

describe("command result transcript blocks", () => {
  it("preserves command identity, tone, and content while deduplicating by key", () => {
    const block: TranscriptBlock = {
      key: "command_1",
      kind: "command_result",
      command: "usage",
      presentation: {
        kind: "lines",
        title: "/usage",
        rows: [{ label: "", value: "No usage recorded yet." }],
        tone: "info",
      },
    };

    expect(mergeTranscriptBlocks([], [block], [block])).toEqual([block]);
  });
});
