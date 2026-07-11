import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { ActiveTurn } from "../../src/components/transcript/ActiveTurn.js";
import { Transcript } from "../../src/components/transcript/Transcript.js";
import type {
  LiveTranscriptProjection,
  TranscriptBlock,
} from "../../src/transcript/model.js";

const blocks: TranscriptBlock[] = [
  {
    key: "omitted",
    kind: "omitted_history",
    message: "Earlier transcript omitted",
  },
  { key: "user", kind: "user", text: "question" },
  { key: "assistant", kind: "assistant", text: "durable answer" },
  { key: "direct", kind: "direct_command", command: "git status" },
  {
    key: "tool",
    kind: "tools",
    items: [
      {
        call_id: "call_1",
        name: "execute",
        outcome: "error",
        summary: "failed safely",
        duration_ms: 12,
        error_code: "exit_1",
      },
    ],
  },
];

describe("scrollback transcript components", () => {
  it.each([
    40, 60, 120,
  ])("preserves essential content at %i columns", (width) => {
    const view = render(<Transcript blocks={blocks} width={width} />);
    expect(view.lastFrame()).toContain("question");
    expect(view.lastFrame()).toContain("durable answer");
    expect(view.lastFrame()).toContain("git status");
    expect(view.lastFrame()).toContain("failed safely");
    expect(view.lastFrame()).toContain("exit_1");
  });

  it("updates only the active projection and hides reasoning when terminal", () => {
    const live: LiveTranscriptProjection = {
      operation_id: "operation_1",
      turn_id: "turn_1",
      reasoning_text: "live reasoning",
      terminal: false,
      blocks: [{ key: "live", kind: "assistant", text: "first" }],
    };
    const view = render(<ActiveTurn live={live} width={80} />);
    expect(view.lastFrame()).toContain("live reasoning");
    view.rerender(
      <ActiveTurn
        live={{
          ...live,
          blocks: [{ key: "live", kind: "assistant", text: "second" }],
        }}
        width={80}
      />,
    );
    expect(view.lastFrame()).toContain("second");
    view.rerender(
      <ActiveTurn
        live={{ ...live, terminal: true, reasoning_text: "must disappear" }}
        width={80}
      />,
    );
    expect(view.lastFrame()).not.toContain("must disappear");
  });
});
