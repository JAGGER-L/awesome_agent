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
  {
    key: "command:command_1",
    kind: "command_input",
    submission_id: "command_1",
    text: "/status",
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
        verb: "Run",
        outcome: "error",
        summary: "failed safely",
        detail: "full bounded output",
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
    expect(view.lastFrame()).toContain("/status");
    expect(view.lastFrame()).toContain("durable answer");
    expect(view.lastFrame()).toContain("git status");
    expect(view.lastFrame()).toContain("1 tool call · 12ms · Ctrl+O to expand");
    expect(view.lastFrame()).not.toContain("failed safely");
    expect(view.lastFrame()).not.toContain("exit_1");
    expect(view.lastFrame()).not.toContain("You");
    expect(view.lastFrame()).not.toContain("Assistant");
  });

  it("updates the active projection and retains completed thinking until reconciliation", () => {
    const live: LiveTranscriptProjection = {
      operation_id: "operation_1",
      turn_id: "turn_1",
      terminal: false,
      blocks: [
        { key: "thought", kind: "thinking", text: "live reasoning" },
        { key: "live", kind: "assistant", text: "first" },
      ],
    };
    const view = render(<ActiveTurn live={live} width={80} />);
    expect(view.lastFrame()).toContain("live reasoning");
    view.rerender(
      <ActiveTurn
        live={{
          ...live,
          blocks: [
            { key: "thought", kind: "thinking", text: "live reasoning" },
            { key: "live", kind: "assistant", text: "second" },
          ],
        }}
        width={80}
      />,
    );
    expect(view.lastFrame()).toContain("second");
    view.rerender(
      <ActiveTurn
        live={{
          ...live,
          terminal: true,
          blocks: [
            {
              key: "thought",
              kind: "thinking",
              text: "must remain",
              duration_ms: 1200,
            },
          ],
        }}
        width={80}
      />,
    );
    expect(view.lastFrame()).toContain("Thought for 1.2 s");
    expect(view.lastFrame()).not.toContain("must remain");
  });

  it("folds tool detail by default and expands it through one prop", () => {
    const collapsed = render(<Transcript blocks={blocks} width={80} />);
    expect(collapsed.lastFrame()).not.toContain("full bounded output");
    const expanded = render(
      <Transcript blocks={blocks} width={80} detailsExpanded />,
    );
    expect(expanded.lastFrame()).toContain("full bounded output");
  });

  it("folds repeated runtime diagnostics and expands one bounded detail", () => {
    const warning = {
      key: "warning:retry:1",
      kind: "warning" as const,
      code: "provider_retry",
      message: "Provider retrying after a transient failure.",
      count: 18,
    };
    const collapsed = render(
      <Transcript blocks={[warning]} width={80} />,
    ).lastFrame();
    expect(collapsed).toContain("× 18 UI diagnostic · Ctrl+O to expand");
    expect(collapsed).not.toContain("Provider retrying");
    const expanded = render(
      <Transcript blocks={[warning]} width={80} detailsExpanded />,
    ).lastFrame();
    expect(expanded).toContain(
      "provider_retry · Provider retrying after a transient failure.",
    );
  });

  it("renders structured tool facts and measured duration", () => {
    const frame =
      render(
        <Transcript
          width={80}
          detailsExpanded
          blocks={[
            {
              key: "write",
              kind: "tools",
              items: [
                {
                  call_id: "call_write",
                  name: "write_file",
                  verb: "Write",
                  target: "circle_area.py",
                  outcome: "success",
                  presentation_outcome: "Created",
                  summary: "21 lines",
                  duration_ms: 18,
                },
              ],
            },
          ]}
        />,
      ).lastFrame() ?? "";

    expect(frame).toContain("● Write circle_area.py");
    expect(frame).toContain("└ Created · 21 lines · 18ms");
  });

  it("keeps incomplete streaming Markdown readable without completed parsing", () => {
    const live: LiveTranscriptProjection = {
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
          thinking_sequence: 0,
          timeline: [
            {
              kind: "assistant",
              id: "assistant:turn_1",
              text: "streaming answer",
            },
          ],
        },
      },
    });
    const frame =
      render(
        <App store={store} reportFatal={() => undefined} width={80} />,
      ).lastFrame() ?? "";

    expect(frame.indexOf("streaming answer")).toBeLessThan(
      frame.indexOf("Message"),
    );
  });

  it("hides token annotations at 40 columns", () => {
    const live: LiveTranscriptProjection = {
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
