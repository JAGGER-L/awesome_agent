import { describe, expect, it } from "vitest";

import { CommandController } from "../../src/commands/controller.js";
import type { MethodName, MethodParams } from "../../src/protocol/methods.js";

describe("auth command flow", () => {
  it("keeps Provider and source selections explicit", async () => {
    const argumentsSeen: (readonly string[])[] = [];
    const controller = new CommandController({
      request: async <Method extends MethodName>(
        _method: Method,
        params: MethodParams[Method],
      ) => {
        const args = "arguments" in params ? (params.arguments ?? []) : [];
        argumentsSeen.push(args);
        const value =
          args.length === 0
            ? selection("Authentication", ["deepseek", "kimi", "mem0"])
            : args.length === 1
              ? selection("DeepSeek credential source", [
                  "environment",
                  "awesome",
                ])
              : {
                  kind: "interaction",
                  interaction: {
                    kind: "secret",
                    provider: "deepseek",
                    action: "add",
                    label: "DeepSeek API Key",
                    environment_variable: "DEEPSEEK_API_KEY",
                    help_url: "https://example.com",
                  },
                };
        return { ok: true, value } as never;
      },
    });

    const providers = await controller.submit(
      { kind: "command", intent: { name: "auth" } },
      "thread_1",
    );
    expect(providers.kind).toBe("selection");
    if (providers.kind !== "selection") return;
    const sources = await controller.select(
      providers.intent,
      "deepseek",
      "thread_1",
    );
    expect(sources.kind).toBe("selection");
    if (sources.kind !== "selection") return;
    const secret = await controller.select(
      sources.intent,
      "awesome",
      "thread_1",
    );
    expect(secret).toMatchObject({
      kind: "secret",
      prompt: { provider: "deepseek", action: "add" },
    });
    expect(argumentsSeen).toEqual([[], ["deepseek"], ["deepseek", "awesome"]]);
  });
});

function selection(prompt: string, values: readonly string[]) {
  return {
    kind: "interaction",
    interaction: {
      kind: "selection",
      prompt,
      options: values.map((value) => ({
        value,
        label: value,
        selected: false,
        disabled: false,
      })),
    },
  };
}
