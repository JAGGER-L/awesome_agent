import { describe, expect, it } from "vitest";

import { CommandController } from "../../src/commands/controller.js";
import type { MethodName, MethodParams } from "../../src/protocol/methods.js";

describe("model command flow", () => {
  it("uses the selected Provider credential path without choosing another source", async () => {
    const controller = new CommandController({
      request: async <Method extends MethodName>(
        _method: Method,
        params: MethodParams[Method],
      ) => {
        const args = "arguments" in params ? (params.arguments ?? []) : [];
        return {
          ok: true,
          value:
            args.length === 0
              ? {
                  kind: "interaction",
                  interaction: {
                    kind: "selection",
                    prompt: "Select Provider",
                    options: [
                      {
                        value: "deepseek",
                        label: "DeepSeek",
                        selected: false,
                        disabled: false,
                      },
                    ],
                  },
                }
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
                },
        } as never;
      },
    });
    const providers = await controller.submit(
      { kind: "command", intent: { name: "model" } },
      "thread_1",
    );
    expect(providers.kind).toBe("selection");
    if (providers.kind !== "selection") return;
    await expect(
      controller.select(providers.intent, "deepseek", "thread_1"),
    ).resolves.toMatchObject({
      kind: "secret",
      prompt: { provider: "deepseek", action: "add" },
    });
  });
});
