import { describe, expect, it } from "vitest";

import { CommandController } from "../../src/commands/controller.js";
import type { MethodName, MethodParams } from "../../src/protocol/methods.js";

describe("memory command flow", () => {
  it("resubmits the layer and On/Off choices as one immutable command path", async () => {
    const argumentsSeen: (readonly string[])[] = [];
    const controller = new CommandController({
      request: async <Method extends MethodName>(
        method: Method,
        params: MethodParams[Method],
      ) => {
        expect(method).toBe("command.execute");
        const arguments_ =
          "arguments" in params ? (params.arguments ?? []) : [];
        argumentsSeen.push(arguments_);
        const value =
          arguments_.length === 0
            ? selection("Choose a memory system.", ["local", "mem0"])
            : arguments_.length === 1
              ? selection("Local memory", ["off", "on"])
              : {
                  kind: "result",
                  payload: {
                    kind: "memory_status",
                    local_available: true,
                    local_enabled: true,
                    cloud_provider: "mem0",
                    cloud_available: false,
                    cloud_enabled: false,
                  },
                };
        return { ok: true, value } as never;
      },
    });

    const layers = await controller.submit(
      { kind: "command", intent: { name: "memory" } },
      "thread_1",
    );
    expect(layers.kind).toBe("selection");
    if (layers.kind !== "selection") return;
    const states = await controller.select(layers.intent, "local", "thread_1");
    expect(states.kind).toBe("selection");
    if (states.kind !== "selection") return;
    const enabled = await controller.select(states.intent, "on", "thread_1");
    expect(enabled).toMatchObject({
      kind: "result",
      payload: { kind: "memory_status", local_enabled: true },
    });
    expect(argumentsSeen).toEqual([[], ["local"], ["local", "on"]]);
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
