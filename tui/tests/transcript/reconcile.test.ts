import { describe, expect, it } from "vitest";

import type { MethodValue } from "../../src/protocol/index.js";
import type { LiveTranscriptProjection } from "../../src/transcript/model.js";
import { reconcileTerminalTurn } from "../../src/transcript/reconcile.js";

const now = "2026-07-11T08:00:00Z";
const live: LiveTranscriptProjection = {
  operation_id: "operation_1",
  turn_id: "turn_1",
  terminal: true,
  blocks: [
    {
      key: "live:thinking",
      kind: "thinking",
      text: "bounded reasoning",
      duration_ms: 1200,
    },
    {
      key: "live:tools",
      kind: "tools",
      items: [
        {
          call_id: "call_1",
          name: "read_file",
          verb: "Read",
          outcome: "success",
          summary: "live",
          detail: "file config.py",
          duration_ms: 0,
        },
      ],
    },
    { key: "live:assistant", kind: "assistant", text: "durable answer" },
    { key: "live:worked", kind: "worked", duration_ms: 2200 },
  ],
};

function page(): MethodValue["thread.read"] {
  return {
    has_more: false,
    view: {
      thread: {
        id: "thread_1",
        workspace_key: "ws_1",
        title: "Thread",
        title_source: "automatic",
        thinking_enabled: false,
        skill_mode: "auto",
        created_at: now,
        updated_at: now,
      },
      entries: [
        {
          id: "entry_user",
          thread_id: "thread_1",
          sequence: 1,
          kind: "user_message",
          content: "question",
          client_message_id: "client_1",
          metadata: {},
          created_at: now,
        },
        {
          id: "entry_assistant",
          thread_id: "thread_1",
          sequence: 2,
          kind: "assistant_message",
          content: "durable answer",
          metadata: {},
          created_at: now,
        },
      ],
      turns: [
        {
          id: "turn_1",
          status: "completed",
          assistant_entry_id: "entry_assistant",
        } as never,
      ],
      tool_activities: [
        {
          call_id: "call_1",
          turn_id: "turn_1",
          operation_id: "operation_1",
          sequence: 1,
          tool_name: "read_file",
          outcome: "success",
          result_summary: "durable tool",
          duration_ms: 9,
        } as never,
      ],
    },
    change_sets: [
      {
        change_set_id: "change_1",
        turn_id: "turn_1",
        lifecycle: "sealed",
        changes: [
          {
            kind: "text_file",
            path: "src/a.py",
            change_kind: "updated",
            additions: 2,
            deletions: 1,
          },
        ],
        created_at: now,
      } as never,
    ],
  };
}

describe("reconcileTerminalTurn", () => {
  it("retains safe current-session activity with durable outcomes", () => {
    const result = reconcileTerminalTurn(live, page());
    expect(result).toMatchObject({
      operation_id: "operation_1",
      turn_id: "turn_1",
    });
    expect(result.blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "assistant", text: "durable answer" }),
        expect.objectContaining({
          kind: "thinking",
          text: "bounded reasoning",
        }),
        expect.objectContaining({
          kind: "tools",
          items: [
            expect.objectContaining({
              summary: "durable tool",
              duration_ms: 9,
              detail: "file config.py",
            }),
          ],
        }),
        expect.objectContaining({ kind: "worked", duration_ms: 2200 }),
        expect.objectContaining({ kind: "change", change_set_id: "change_1" }),
      ]),
    );
  });

  it.each([
    ["missing Turn", { ...page(), view: { ...page().view, turns: [] } }],
    [
      "missing Assistant",
      {
        ...page(),
        view: { ...page().view, entries: page().view.entries.slice(0, 1) },
      },
    ],
    [
      "missing Tool",
      { ...page(), view: { ...page().view, tool_activities: [] } },
    ],
  ])("keeps visible transient output when %s", (_name, value) => {
    const result = reconcileTerminalTurn(live, value);
    expect(result.blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "assistant", text: "durable answer" }),
        expect.objectContaining({
          kind: "error",
          code: "transcript_not_reconciled",
        }),
      ]),
    );
  });

  it("is deterministic for a repeated durable page", () => {
    expect(reconcileTerminalTurn(live, page())).toEqual(
      reconcileTerminalTurn(live, page()),
    );
  });

  it("uses the durable assistant when live segments do not match", () => {
    const changed = {
      ...live,
      blocks: live.blocks.map((block) =>
        block.kind === "assistant" ? { ...block, text: "partial" } : block,
      ),
    };
    const result = reconcileTerminalTurn(changed, page());
    expect(result.blocks.filter((block) => block.kind === "assistant")).toEqual(
      [expect.objectContaining({ text: "durable answer" })],
    );
    expect(result.blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "thinking" }),
        expect.objectContaining({ kind: "worked" }),
      ]),
    );
  });

  it("returns only the cancelled Turn projection and retains partial output", () => {
    const cancelledPage = page();
    cancelledPage.view.turns = [
      {
        ...cancelledPage.view.turns[0],
        status: "cancelled",
        assistant_entry_id: undefined,
        termination_reason: "cancelled",
      } as never,
    ];
    cancelledPage.view.entries = cancelledPage.view.entries.slice(0, 1);

    const result = reconcileTerminalTurn(live, cancelledPage);

    expect(result.blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "assistant", text: "durable answer" }),
        expect.objectContaining({ kind: "error", code: "cancelled" }),
      ]),
    );
    expect(result.blocks.some((block) => block.kind === "user")).toBe(false);
  });
});
