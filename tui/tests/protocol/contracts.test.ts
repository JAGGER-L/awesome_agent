import { describe, expect, it } from "vitest";

import { commandOutcomeSchema } from "../../src/protocol/commands.js";
import { methodSchemas } from "../../src/protocol/methods.js";
import { loadFixtureCorpus } from "../contracts/fixture-loader.js";

describe("provider credential protocol", () => {
  it("accepts a dedicated credential request and rejects unknown fields", () => {
    const params = {
      provider: "deepseek",
      action: "add",
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
    expect(
      methodSchemas["provider.credential.set"].params.safeParse({
        provider: "deepseek",
        action: "delete",
      }).success,
    ).toBe(true);
    expect(
      methodSchemas["provider.credential.set"].params.safeParse({
        provider: "deepseek",
        action: "delete",
        api_key: "must-not-be-accepted",
      }).success,
    ).toBe(false);
  });

  it("accepts secret prompts without accepting raw credential fields", () => {
    const result = {
      kind: "interaction",
      interaction: {
        kind: "secret",
        provider: "kimi",
        action: "add",
        label: "Kimi API Key",
        environment_variable: "MOONSHOT_API_KEY",
        help_url: "https://example.com",
      },
    };

    expect(commandOutcomeSchema.safeParse(result).success).toBe(true);
    expect(
      commandOutcomeSchema.safeParse({ ...result, api_key: "secret" }).success,
    ).toBe(false);
  });
});

describe("workspace change protocol", () => {
  it("accepts every structured change delta from the shared fixture", async () => {
    const corpus = await loadFixtureCorpus();
    const methods = corpus.files["methods.valid.json"] as {
      cases: Array<{ name: string; result: unknown }>;
    };
    const fixture = methods.cases.find(({ name }) => name === "thread.read");
    expect(fixture).toBeDefined();

    const result = methodSchemas["thread.read"].result.parse(fixture?.result);
    expect(result.ok && result.value.change_sets[0]?.changes).toEqual([
      expect.objectContaining({
        kind: "text_file",
        additions: 16,
        deletions: 2,
      }),
      expect.objectContaining({ kind: "binary_file" }),
      expect.objectContaining({ kind: "directory" }),
      expect.objectContaining({ kind: "symlink" }),
    ]);
  });
});
