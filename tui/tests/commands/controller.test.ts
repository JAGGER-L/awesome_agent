import { describe, expect, it } from "vitest";

import { CommandController } from "../../src/commands/controller.js";
import type { RoutedInput } from "../../src/commands/parser.js";
import type { MethodName, MethodParams } from "../../src/protocol/methods.js";

type Call = { method: MethodName; params: MethodParams[MethodName] };

function harness(result: unknown = { ok: true, value: {} }) {
  const calls: Call[] = [];
  return {
    calls,
    controller: new CommandController({
      request: async <Method extends MethodName>(
        method: Method,
        params: MethodParams[Method],
      ) => {
        calls.push({ method, params } as Call);
        return result as never;
      },
    }),
  };
}

describe("CommandController", () => {
  it("loads application and thread projections for one atomic replacement", async () => {
    const calls: Call[] = [];
    const controller = new CommandController({
      request: async <Method extends MethodName>(
        method: Method,
        params: MethodParams[Method],
      ) => {
        calls.push({ method, params } as Call);
        return {
          ok: true,
          value:
            method === "application.getState"
              ? { current_thread_id: "thread_new" }
              : { view: { thread: { id: "thread_new" } } },
        } as never;
      },
    });

    await expect(
      controller.loadThreadReplacement("thread_new"),
    ).resolves.toMatchObject({
      kind: "replacement",
      application: { current_thread_id: "thread_new" },
      thread: { view: { thread: { id: "thread_new" } } },
    });
    expect(calls).toEqual([
      { method: "application.getState", params: {} },
      {
        method: "thread.read",
        params: { thread_id: "thread_new", limit: 100 },
      },
    ]);
  });
  it.each([
    [
      { kind: "turn", content: "hello" },
      "turn.submit",
      { thread_id: "thread_1", content: "hello" },
    ],
    [
      { kind: "direct", command: "pwd" },
      "direct.execute",
      { thread_id: "thread_1", command: "pwd" },
    ],
    [
      { kind: "command", intent: { name: "status" } },
      "command.execute",
      { name: "status" },
    ],
    [
      { kind: "command", intent: { name: "debug", arguments: ["failure"] } },
      "command.execute",
      { name: "debug", arguments: ["failure"] },
    ],
  ] as const)("routes %s to %s", async (routed, method, params) => {
    const { calls, controller } = harness({
      ok: true,
      value:
        method === "command.execute"
          ? { status: "success", content: "ok", data: {} }
          : { operation_id: "operation_1", thread_id: "thread_1" },
    });
    await controller.submit(routed as RoutedInput, "thread_1");
    expect(calls).toEqual([{ method, params }]);
  });

  it("keeps Ink-local actions out of RPC", async () => {
    const { calls, controller } = harness();
    await expect(
      controller.submit(
        { kind: "local", intent: { name: "help" } },
        "thread_1",
      ),
    ).resolves.toEqual({ kind: "local", intent: { name: "help" } });
    expect(calls).toEqual([]);
  });

  it("requires a thread only for turns and direct commands", async () => {
    const { calls, controller } = harness();
    await expect(
      controller.submit({ kind: "turn", content: "hello" }, undefined),
    ).resolves.toMatchObject({ kind: "error", code: "thread_required" });
    expect(calls).toEqual([]);
  });

  it.each([
    "operation_busy",
    "model_not_configured",
  ])("preserves product failure %s", async (code) => {
    const error = { code, message: code, retryable: true, data: {} };
    const { controller } = harness({ ok: false, error });
    await expect(
      controller.submit({ kind: "turn", content: "hello" }, "thread_1"),
    ).resolves.toEqual({ kind: "error", error });
  });

  it("opens a picker for CommandSelection and submits the selected value fresh", async () => {
    const selection = {
      prompt: "Choose model",
      options: [{ value: "deepseek-chat", label: "DeepSeek", selected: true }],
    };
    const { calls, controller } = harness({
      ok: true,
      value: {
        status: "interaction_required",
        content: "",
        data: {},
        selection,
      },
    });
    await expect(
      controller.submit(
        { kind: "command", intent: { name: "model" } },
        "thread_1",
      ),
    ).resolves.toMatchObject({ kind: "picker", selection });
    await controller.select({ name: "model" }, "deepseek-chat", "thread_1");
    expect(calls.at(-1)).toEqual({
      method: "command.execute",
      params: { name: "model", arguments: ["deepseek-chat"] },
    });
  });

  it("returns a dedicated secret outcome and submits credentials by RPC", async () => {
    const prompt = {
      provider: "deepseek" as const,
      action: "add" as const,
      label: "DeepSeek API Key",
      environment_variable: "DEEPSEEK_API_KEY",
      help_url: "https://example.com",
    };
    const { controller } = harness({
      ok: true,
      value: {
        status: "success",
        content: "",
        data: {},
        secret_prompt: prompt,
      },
    });
    await expect(
      controller.submit(
        { kind: "command", intent: { name: "model", arguments: ["deepseek"] } },
        "thread_1",
      ),
    ).resolves.toMatchObject({ kind: "secret", prompt });

    const credential = harness({
      ok: true,
      value: {
        provider: "deepseek",
        status: "configured",
        source: "user_env_file",
        code: "credential_saved",
      },
    });
    await credential.controller.setCredential(
      "deepseek",
      "add",
      "private",
      false,
    );
    expect(credential.calls).toEqual([
      {
        method: "provider.credential.set",
        params: {
          provider: "deepseek",
          action: "add",
          api_key: "private",
          allow_unverified: false,
        },
      },
    ]);
  });

  it("returns non-selection interaction-required results without inventing state", async () => {
    const value = {
      status: "interaction_required",
      content: "Trust required",
      data: {},
    };
    const { controller } = harness({ ok: true, value });
    await expect(
      controller.submit(
        { kind: "command", intent: { name: "workspace" } },
        undefined,
      ),
    ).resolves.toEqual({ kind: "result", result: value });
  });

  it("rejects a command name outside the catalog before RPC", async () => {
    const { calls, controller } = harness();
    await expect(
      controller.submit(
        { kind: "command", intent: { name: "editor" } } as never,
        undefined,
      ),
    ).resolves.toMatchObject({ kind: "error", code: "unknown_command" });
    expect(calls).toEqual([]);
  });
});
