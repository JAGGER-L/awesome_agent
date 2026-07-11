import { describe, expect, it } from "vitest";

import { parseLaunchIntent } from "../../src/cli/args.js";
import {
  runStartup,
  selectStartupThread,
  StartupError,
} from "../../src/surface/startup.js";
import type {
  MethodName,
  MethodParams,
  MethodValue,
} from "../../src/protocol/methods.js";

type Call = { method: MethodName; params: MethodParams[MethodName] };

const thread = (id: string): MethodValue["thread.list"]["threads"][number] => ({
  id,
  workspace_key: "workspace_1",
  title: `Title ${id}`,
  current_model: "deepseek/deepseek-chat",
  thinking_enabled: false,
  skill_mode: "auto",
  created_at: "2026-07-11T00:00:00Z",
  updated_at: "2026-07-11T01:00:00Z",
});

const threadPage = (id: string): MethodValue["thread.read"] => ({
  view: {
    thread: thread(id),
    entries: [],
    turns: [],
    tool_activities: [],
  },
  change_sets: [],
  has_more: false,
});

function harness({
  recent = [thread("thread_recent")],
  resumeSelection,
  commandFailure,
}: {
  recent?: MethodValue["thread.list"]["threads"];
  resumeSelection?: MethodValue["command.execute"]["selection"];
  commandFailure?: boolean;
} = {}) {
  const calls: Call[] = [];
  return {
    calls,
    surface: {
      request: async <Method extends MethodName>(
        method: Method,
        params: MethodParams[Method],
      ) => {
        calls.push({ method, params } as Call);
        if (method === "thread.list") {
          return {
            ok: true,
            value: { threads: recent, has_more: false },
          } as never;
        }
        if (method === "thread.read") {
          return {
            ok: true,
            value: threadPage((params as { thread_id: string }).thread_id),
          } as never;
        }
        if (method === "command.execute") {
          if (commandFailure) {
            return {
              ok: false,
              error: {
                code: "thread_not_found",
                message: "missing",
                retryable: false,
                data: {},
              },
            } as never;
          }
          const intent = params as MethodParams["command.execute"];
          if (intent.name === "resume" && resumeSelection) {
            return {
              ok: true,
              value: {
                status: "success",
                content: "",
                data: {},
                selection: resumeSelection,
              },
            } as never;
          }
          const id =
            intent.name === "new"
              ? "thread_new"
              : (intent.arguments?.[0] ?? "thread_recent");
          return {
            ok: true,
            value: {
              status: "success",
              content: "",
              data: { thread_id: id, title: `Title ${id}` },
            },
          } as never;
        }
        throw new Error(`Unexpected method ${method}`);
      },
    },
  };
}

describe("parseLaunchIntent", () => {
  it.each([
    [[], { kind: "new" }],
    [["--continue"], { kind: "continue" }],
    [["--resume"], { kind: "resume-picker" }],
    [
      ["--resume", "thread_abcd1234"],
      { kind: "resume", threadId: "thread_abcd1234" },
    ],
  ] as const)("parses %j", (argv, expected) => {
    expect(parseLaunchIntent([...argv])).toEqual(expected);
  });

  it.each([
    ["--continue", "--resume"],
    ["--resume", "a", "b"],
    ["--unknown"],
  ])("rejects invalid launch arguments %j", (...argv) => {
    expect(() => parseLaunchIntent(argv)).toThrow();
  });
});

describe("runStartup", () => {
  it("creates exactly one new thread and hydrates it", async () => {
    const { calls, surface } = harness();
    await expect(runStartup(surface, { kind: "new" })).resolves.toMatchObject({
      kind: "ready",
      thread: { view: { thread: { id: "thread_new" } } },
    });
    expect(calls).toEqual([
      { method: "command.execute", params: { name: "new" } },
      { method: "thread.read", params: { thread_id: "thread_new", limit: 50 } },
    ]);
  });

  it("continues the first recent thread through Python ownership", async () => {
    const { calls, surface } = harness();
    await runStartup(surface, { kind: "continue" });
    expect(calls.map(({ method }) => method)).toEqual([
      "thread.list",
      "command.execute",
      "thread.read",
    ]);
    expect(calls[1]).toEqual({
      method: "command.execute",
      params: { name: "resume", arguments: ["thread_recent"] },
    });
  });

  it("creates one thread when continue has an empty workspace", async () => {
    const { calls, surface } = harness({ recent: [] });
    await runStartup(surface, { kind: "continue" });
    expect(calls.filter(({ method }) => method === "command.execute")).toEqual([
      { method: "command.execute", params: { name: "new" } },
    ]);
  });

  it.each([
    "thread_full_identifier",
    "thread_abcd1234",
  ])("resumes exact or prefix identity %s", async (threadId) => {
    const { calls, surface } = harness();
    await runStartup(surface, { kind: "resume", threadId });
    expect(calls[0]).toEqual({
      method: "command.execute",
      params: { name: "resume", arguments: [threadId] },
    });
  });

  it("returns an ambiguous resume selection without hydrating arbitrarily", async () => {
    const selection = {
      prompt: "Select",
      options: [
        { value: "thread_a", label: "A", selected: false },
        { value: "thread_b", label: "B", selected: false },
      ],
    };
    const { calls, surface } = harness({ resumeSelection: selection });
    await expect(
      runStartup(surface, { kind: "resume", threadId: "thread_abcd1234" }),
    ).resolves.toEqual({ kind: "selection_required", selection });
    expect(calls.some(({ method }) => method === "thread.read")).toBe(false);
  });

  it("opens the recent picker and creates one thread when it is empty", async () => {
    const selection = {
      prompt: "Select",
      options: [{ value: "thread_a", label: "A", selected: false }],
    };
    const picker = harness({ resumeSelection: selection });
    await expect(
      runStartup(picker.surface, { kind: "resume-picker" }),
    ).resolves.toEqual({ kind: "selection_required", selection });

    const empty = harness({ recent: [] });
    empty.surface.request = async (method, params) => {
      empty.calls.push({ method, params } as Call);
      if (
        method === "command.execute" &&
        (params as { name: string }).name === "resume"
      ) {
        return {
          ok: true,
          value: { status: "success", content: "", data: { threads: [] } },
        } as never;
      }
      if (method === "command.execute") {
        return {
          ok: true,
          value: {
            status: "success",
            content: "",
            data: { thread_id: "thread_new" },
          },
        } as never;
      }
      return { ok: true, value: threadPage("thread_new") } as never;
    };
    await runStartup(empty.surface, { kind: "resume-picker" });
    expect(
      empty.calls.filter(({ method }) => method === "command.execute"),
    ).toHaveLength(2);
  });

  it("hydrates a selected option through a fresh resume command", async () => {
    const { calls, surface } = harness();
    await selectStartupThread(surface, "thread_selected");
    expect(calls).toEqual([
      {
        method: "command.execute",
        params: { name: "resume", arguments: ["thread_selected"] },
      },
      {
        method: "thread.read",
        params: { thread_id: "thread_selected", limit: 50 },
      },
    ]);
  });

  it("propagates typed thread product failures", async () => {
    const { surface } = harness({ commandFailure: true });
    await expect(
      runStartup(surface, { kind: "resume", threadId: "thread_missing" }),
    ).rejects.toBeInstanceOf(StartupError);
  });
});
