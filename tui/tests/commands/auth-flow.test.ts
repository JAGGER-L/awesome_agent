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
            ? selection("Authentication", [
                "deepseek",
                "kimi",
                "mem0",
                "tavily",
              ])
            : args.length === 1
              ? selection("Tavily credential source", [
                  "environment",
                  "awesome",
                ])
              : {
                  kind: "interaction",
                  interaction: {
                    kind: "secret",
                    provider: "tavily",
                    action: "add",
                    label: "Tavily API Key",
                    environment_variable: "TAVILY_API_KEY",
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
      "tavily",
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
      prompt: { provider: "tavily", action: "add" },
    });
    expect(argumentsSeen).toEqual([[], ["tavily"], ["tavily", "awesome"]]);
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
