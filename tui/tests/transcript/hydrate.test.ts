import { describe, expect, it } from "vitest";

import type { MethodValue } from "../../src/protocol/index.js";
import { hydrateThreadPage } from "../../src/transcript/hydrate.js";

function page(): MethodValue["thread.read"] {
  const now = "2026-07-11T08:00:00Z";
  return {
    has_more: true,
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
          content: "Inspect",
          client_message_id: "client_1",
          metadata: {},
          created_at: now,
        },
        {
          id: "entry_assistant",
          thread_id: "thread_1",
          sequence: 2,
          kind: "assistant_message",
          content: "Done",
          metadata: {},
          created_at: now,
        },
        {
          id: "entry_direct",
          thread_id: "thread_1",
          sequence: 3,
          kind: "direct_command",
          content: "git status",
          metadata: { operation_id: "operation_direct" },
          created_at: now,
        },
      ],
      turns: [
        {
          id: "turn_1",
          assistant_entry_id: "entry_assistant",
          status: "completed",
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
          result_summary: "Read file",
          duration_ms: 12,
        } as never,
        {
          call_id: "call_2",
          operation_id: "operation_direct",
          sequence: 2,
          tool_name: "execute",
          outcome: "error",
          result_summary: "Command failed",
          error_code: "exit_1",
          duration_ms: 20,
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

describe("hydrateThreadPage", () => {
  it("projects one bounded durable page in deterministic order", () => {
    const projection = hydrateThreadPage(page());
    expect(projection.blocks.map((block) => block.kind)).toEqual([
      "omitted_history",
      "user",
      "tools",
      "change",
      "assistant",
      "direct_command",
      "tools",
    ]);
    expect(projection.blocks[0]).toMatchObject({ key: "history:omitted" });
    expect(projection.blocks[1]).toMatchObject({
      key: "user:client_1",
      client_message_id: "client_1",
      status: "persisted",
    });
    expect(projection.blocks[2]).toMatchObject({
      kind: "tools",
      items: [{ name: "read_file", summary: "Read file" }],
    });
    expect(projection.blocks[3]).toMatchObject({
      kind: "change",
      changes: [expect.objectContaining({ path: "src/a.py" })],
    });
    expect(projection.blocks[6]).toMatchObject({
      kind: "tools",
      items: [{ outcome: "error", error_code: "exit_1" }],
    });
  });

  it("does not add an omission block when the page is complete", () => {
    const complete = { ...page(), has_more: false };
    expect(
      hydrateThreadPage(complete).blocks.some(
        (block) => block.kind === "omitted_history",
      ),
    ).toBe(false);
  });

  it("hydrates durable summaries without ephemeral thinking or details", () => {
    const projection = hydrateThreadPage(page());
    expect(
      projection.blocks.some(
        (block) => block.kind === "thinking" || block.kind === "worked",
      ),
    ).toBe(false);
    const tools = projection.blocks.filter((block) => block.kind === "tools");
    expect(tools.flatMap((block) => block.items)).toEqual(
      expect.not.arrayContaining([
        expect.objectContaining({ detail: expect.anything() }),
      ]),
    );
  });
});
