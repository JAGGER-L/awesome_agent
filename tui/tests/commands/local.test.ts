import { describe, expect, it, vi } from "vitest";

import {
  LocalCommandService,
  type LocalCommandDependencies,
} from "../../src/commands/local.js";
import type { MethodValue } from "../../src/protocol/methods.js";

const thread = (): MethodValue["thread.read"] => ({
  view: {
    thread: {
      id: "thread_1",
      workspace_key: "workspace_1",
      title: "Thread",
      current_model: "deepseek/deepseek-v4-flash",
      thinking_enabled: false,
      skill_mode: "auto",
      created_at: "2026-07-11T00:00:00Z",
      updated_at: "2026-07-11T00:00:00Z",
    },
    entries: [
      {
        id: "entry_user",
        thread_id: "thread_1",
        sequence: 1,
        kind: "user_message",
        content: "user text",
        metadata: {},
        created_at: "2026-07-11T00:00:00Z",
      },
      {
        id: "entry_assistant_old",
        thread_id: "thread_1",
        sequence: 2,
        kind: "assistant_message",
        content: "old answer",
        metadata: {},
        created_at: "2026-07-11T00:00:01Z",
      },
      {
        id: "entry_direct",
        thread_id: "thread_1",
        sequence: 3,
        kind: "direct_command",
        content: "tool-like output",
        metadata: {},
        created_at: "2026-07-11T00:00:02Z",
      },
      {
        id: "entry_assistant_latest",
        thread_id: "thread_1",
        sequence: 4,
        kind: "assistant_message",
        content: "latest durable answer",
        metadata: {},
        created_at: "2026-07-11T00:00:03Z",
      },
    ],
    turns: [],
    tool_activities: [],
  },
  change_sets: [],
  has_more: false,
});

function harness(overrides: Partial<LocalCommandDependencies> = {}) {
  const writeText = vi.fn(async () => undefined);
  const saveTheme = vi.fn(async () => undefined);
  const setTheme = vi.fn();
  const dependencies: LocalCommandDependencies = {
    clipboard: { writeText },
    getThread: thread,
    getTheme: () => "system",
    setTheme,
    saveTheme,
    ...overrides,
  };
  return {
    service: new LocalCommandService(dependencies),
    writeText,
    saveTheme,
    setTheme,
  };
}

describe("LocalCommandService", () => {
  it("opens all-command or focused help without RPC", async () => {
    const { service } = harness();
    await expect(service.execute({ name: "help" })).resolves.toEqual({
      kind: "help",
    });
    await expect(
      service.execute({ name: "help", arguments: ["thinking"] }),
    ).resolves.toEqual({ kind: "help", command: "thinking" });
    await expect(
      service.execute({ name: "help", arguments: ["editor"] }),
    ).resolves.toEqual({
      kind: "warning",
      message: "No command named /editor.",
    });
  });

  it("queries theme with a picker and persists an explicit selection", async () => {
    const { service, saveTheme, setTheme } = harness();
    await expect(service.execute({ name: "theme" })).resolves.toMatchObject({
      kind: "picker",
      selection: { prompt: "Theme" },
    });
    await expect(
      service.execute({ name: "theme", arguments: ["dark"] }),
    ).resolves.toEqual({ kind: "notice", message: "Theme changed to dark." });
    expect(saveTheme).toHaveBeenCalledWith("dark");
    expect(setTheme).toHaveBeenCalledWith("dark");
  });

  it("copies only the latest durable Assistant entry", async () => {
    const { service, writeText } = harness();
    await expect(service.execute({ name: "copy" })).resolves.toEqual({
      kind: "notice",
      message: "Copied latest Assistant answer.",
    });
    expect(writeText).toHaveBeenCalledWith("latest durable answer");
  });

  it("warns when no durable Assistant answer exists", async () => {
    const empty = thread();
    empty.view.entries = [];
    const { service } = harness({ getThread: () => empty });
    await expect(service.execute({ name: "copy" })).resolves.toMatchObject({
      kind: "warning",
    });
  });

  it("reports clipboard failure without content or OSC52 fallback", async () => {
    const { service } = harness({
      clipboard: {
        writeText: async () => {
          throw new Error("unavailable");
        },
      },
    });
    await expect(service.execute({ name: "copy" })).resolves.toEqual({
      kind: "warning",
      message: "Clipboard is unavailable.",
    });
  });

  it("emits only a shutdown intent for quit", async () => {
    const { service } = harness();
    await expect(service.execute({ name: "quit" })).resolves.toEqual({
      kind: "shutdown",
    });
  });
});
