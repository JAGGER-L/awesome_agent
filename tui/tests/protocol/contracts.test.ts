import { describe, expect, it } from "vitest";

import { commandResultSchema } from "../../src/protocol/commands.js";
import { methodSchemas } from "../../src/protocol/methods.js";

describe("provider credential protocol", () => {
  it("accepts a dedicated credential request and rejects unknown fields", () => {
    const params = {
      provider: "deepseek",
      api_key: "never-render-this",
    };

    expect(
      methodSchemas["provider.credential.set"].params.safeParse(params).success,
    ).toBe(true);
    expect(
      methodSchemas["provider.credential.set"].params.safeParse({
        ...params,
        extra: true,
      }).success,
    ).toBe(false);
  });

  it("accepts secret prompts without accepting raw credential fields", () => {
    const result = {
      status: "success",
      content: "",
      data: {},
      secret_prompt: {
        provider: "kimi",
        action: "add",
        label: "Kimi API Key",
        environment_variable: "MOONSHOT_API_KEY",
        help_url: "https://example.com",
      },
    };

    expect(commandResultSchema.safeParse(result).success).toBe(true);
    expect(
      commandResultSchema.safeParse({ ...result, api_key: "secret" }).success,
    ).toBe(false);
  });
});
