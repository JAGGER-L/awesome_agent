import { describe, expect, it } from "vitest";

import type { MethodValue } from "../../src/protocol/index.js";
import type { LiveTranscriptProjection } from "../../src/transcript/model.js";
import { reconcileCompletedTurn } from "../../src/transcript/reconcile.js";

const now = "2026-07-11T08:00:00Z";
const live: LiveTranscriptProjection = {
  operation_id: "operation_1",
  turn_id: "turn_1",
  reasoning_text: "",
  terminal: true,
  blocks: [
    { key: "live:assistant", kind: "assistant", text: "coalesced" },
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
          duration_ms: 0,
        },
      ],
    },
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
        changed_paths: ["src/a.py"],
        reversibility: "full",
        created_at: now,
      } as never,
    ],
  };
}

describe("reconcileCompletedTurn", () => {
  it("replaces transient text/tools with durable authority", () => {
    const result = reconcileCompletedTurn(live, page());
    expect(result.persisted).toBe(true);
    expect(result.blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "assistant", text: "durable answer" }),
        expect.objectContaining({
          kind: "tools",
          items: [expect.objectContaining({ summary: "durable tool" })],
        }),
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
    const result = reconcileCompletedTurn(live, value);
    expect(result.persisted).toBe(false);
    expect(result.blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "assistant", text: "coalesced" }),
        expect.objectContaining({
          kind: "error",
          code: "transcript_not_reconciled",
        }),
      ]),
    );
  });

  it("is deterministic for a repeated durable page", () => {
    expect(reconcileCompletedTurn(live, page())).toEqual(
      reconcileCompletedTurn(live, page()),
    );
  });
});
