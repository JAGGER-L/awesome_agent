import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { ActiveTurn } from "../../src/components/transcript/ActiveTurn.js";
import { Transcript } from "../../src/components/transcript/Transcript.js";
import { App } from "../../src/app/App.js";
import { initialSurfaceState } from "../../src/state/reducer.js";
import { createSurfaceStore } from "../../src/state/store.js";
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
  {
    key: "user:client_1",
    kind: "user",
    client_message_id: "client_1",
    status: "persisted",
    text: "question",
  },
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
    expect(view.lastFrame()).not.toContain("You");
    expect(view.lastFrame()).not.toContain("Assistant");
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

  it("keeps incomplete streaming Markdown readable without completed parsing", () => {
    const live: LiveTranscriptProjection = {
      reasoning_text: "",
      terminal: false,
      blocks: [
        {
          key: "live",
          kind: "assistant",
          text: "# Heading\n\n- item\n\n**partial",
        },
      ],
    };
    const frame =
      render(<ActiveTurn live={live} width={80} />).lastFrame() ?? "";
    expect(frame).toContain("Heading");
    expect(frame).toContain("• item");
    expect(frame).toContain("**partial");
    expect(frame).not.toContain("# Heading");
  });

  it("keeps the composer after the active turn projection", () => {
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      active_operation: {
        id: "operation_1",
        status: "active",
        turn: {
          id: "turn_1",
          status: "active",
          started_at: "2026-07-12T00:00:00Z",
          assistant_text: "streaming answer",
          reasoning_text: "",
          reasoning_seen: false,
          tools: {},
          tool_order: [],
        },
      },
    });
    const frame = render(<App store={store} width={80} />).lastFrame() ?? "";

    expect(frame.indexOf("streaming answer")).toBeLessThan(
      frame.indexOf("Message"),
    );
  });

  it("hides token annotations at 40 columns", () => {
    const live: LiveTranscriptProjection = {
      reasoning_text: "",
      terminal: false,
      usage: { input_tokens: 10, output_tokens: 2 },
      blocks: [{ key: "live", kind: "assistant", text: "essential" }],
    };
    const narrow = render(<ActiveTurn live={live} width={40} />);
    expect(narrow.lastFrame()).toContain("essential");
    expect(narrow.lastFrame()).not.toContain("Tokens");
    const wide = render(<ActiveTurn live={live} width={60} />);
    expect(wide.lastFrame()).toContain("Tokens 10 in · 2 out");
  });
});
