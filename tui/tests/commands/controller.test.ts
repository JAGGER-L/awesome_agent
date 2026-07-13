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
  it.each([
    [
      { kind: "turn", content: "hello" },
      "turn.submit",
      {
        thread_id: "thread_1",
        content: "hello",
        client_message_id: "client_1",
      },
    ],
    [
      { kind: "direct", command: "pwd" },
      "direct.execute",
      { thread_id: "thread_1", command: "pwd" },
    ],
  ] as const)("routes %s to %s", async (routed, method, params) => {
    const { calls, controller } = harness({
      ok: true,
      value: { operation_id: "operation_1", thread_id: "thread_1" },
    });
    await controller.submit(routed as RoutedInput, "thread_1", "client_1");
    expect(calls).toEqual([{ method, params }]);
  });

  it("maps every typed command outcome discriminator", async () => {
    const payload = { kind: "workspace", path: "E:\\workspace" } as const;
    await expect(
      harness({
        ok: true,
        value: { kind: "result", payload },
      }).controller.submit(
        { kind: "command", intent: { name: "workspace" } },
        "thread_1",
      ),
    ).resolves.toEqual({ kind: "result", payload });

    const selection = {
      kind: "selection" as const,
      prompt: "Choose model",
      options: [
        {
          value: "deepseek",
          label: "DeepSeek",
          selected: true,
          disabled: false,
        },
      ],
    };
    await expect(
      harness({
        ok: true,
        value: { kind: "interaction", interaction: selection },
      }).controller.submit(
        { kind: "command", intent: { name: "model" } },
        "thread_1",
      ),
    ).resolves.toMatchObject({ kind: "selection", selection });

    const prompt = {
      kind: "secret" as const,
      provider: "deepseek" as const,
      action: "add" as const,
      label: "DeepSeek API Key",
      environment_variable: "DEEPSEEK_API_KEY",
      help_url: "https://example.com",
    };
    await expect(
      harness({
        ok: true,
        value: { kind: "interaction", interaction: prompt },
      }).controller.submit(
        { kind: "command", intent: { name: "auth" } },
        "thread_1",
      ),
    ).resolves.toMatchObject({ kind: "secret", prompt });

    await expect(
      harness({
        ok: true,
        value: { kind: "error", code: "invalid_arguments", message: "Usage" },
      }).controller.submit(
        { kind: "command", intent: { name: "status" } },
        "thread_1",
      ),
    ).resolves.toEqual({
      kind: "command_error",
      code: "invalid_arguments",
      message: "Usage",
    });
  });

  it("keeps Ink commands local and rejects unknown commands before RPC", async () => {
    const { calls, controller } = harness();
    await expect(
      controller.submit(
        { kind: "local", intent: { name: "help" } },
        "thread_1",
      ),
    ).resolves.toEqual({ kind: "local", intent: { name: "help" } });
    await expect(
      controller.submit(
        { kind: "command", intent: { name: "editor" } } as never,
        undefined,
      ),
    ).resolves.toMatchObject({ kind: "error", code: "unknown_command" });
    expect(calls).toEqual([]);
  });

  it("returns the authoritative Thread transition without follow-up reads", async () => {
    const payload = {
      kind: "thread_transition" as const,
      transition: {
        reason: "new" as const,
        application: { current_thread_id: "thread_new" },
        thread: { view: { thread: { id: "thread_new" } } },
      },
    } as never;
    const { calls, controller } = harness({
      ok: true,
      value: { kind: "result", payload },
    });
    await expect(
      controller.submit(
        { kind: "command", intent: { name: "new" } },
        "thread_old",
      ),
    ).resolves.toEqual({ kind: "result", payload });
    expect(calls.map((call) => call.method)).toEqual(["command.execute"]);
  });
});
