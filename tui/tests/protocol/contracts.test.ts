import { describe, expect, it } from "vitest";

import { commandOutcomeSchema } from "../../src/protocol/commands.js";
import { productErrorSchema } from "../../src/protocol/base.js";
import { eventEnvelopeSchema } from "../../src/protocol/events.js";
import { methodSchemas } from "../../src/protocol/methods.js";
import { PRODUCT_VERSION } from "../../src/version.js";
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

describe("startup state recovery protocol", () => {
  it("accepts Python-produced reset initialization and responses", async () => {
    const corpus = await loadFixtureCorpus();
    const methods = corpus.files["methods.valid.json"] as {
      cases: Array<{ name: string; params: unknown; result: unknown }>;
    };
    const initialize = methods.cases.find(
      ({ name }) => name === "initialize.state_reset_required",
    );
    const accepted = methods.cases.find(
      ({ name }) => name === "interaction.respond.state_reset",
    );
    const denied = methods.cases.find(
      ({ name }) => name === "interaction.respond.state_reset_denied",
    );

    expect(methodSchemas.initialize.result.parse(initialize?.result)).toEqual({
      ok: true,
      value: {
        capabilities: ["threads", "turns", "commands"],
        interaction_id: "interaction_state_reset",
        product_version: PRODUCT_VERSION,
        protocol_version: 2,
        session_id: "session_11111111111111111111111111111111",
        status: "state_reset_required",
        workspace: { display_path: "C:\\workspace" },
      },
    });
    expect(
      methodSchemas["interaction.respond"].params.parse(accepted?.params),
    ).toEqual({
      interaction_id: "interaction_state_reset",
      decision: "reset_state",
    });
    expect(
      methodSchemas["interaction.respond"].result.parse(accepted?.result),
    ).toEqual({ ok: true, value: { accepted: true, status: "resolved" } });
    expect(
      methodSchemas["interaction.respond"].result.parse(denied?.result),
    ).toEqual({ ok: true, value: { accepted: true, status: "denied" } });
  });

  it("accepts the reset interaction Event emitted by Python", async () => {
    const corpus = await loadFixtureCorpus();
    const events = corpus.files["events.valid.json"] as {
      events: Array<{ event_type: string }>;
    };
    const interaction = events.events.find(
      ({ event_type }) => event_type === "interaction.required",
    );

    expect(eventEnvelopeSchema.parse(interaction)).toEqual(
      expect.objectContaining({
        payload: expect.objectContaining({
          interaction_kind: "state_reset",
          choices: [
            {
              decision: "reset_state",
              label: "Reset local state and continue",
              description: undefined,
            },
            { decision: "deny", label: "Exit", description: undefined },
          ],
        }),
      }),
    );
  });

  it("strictly validates every storage diagnostic", async () => {
    const corpus = await loadFixtureCorpus();
    const failures = corpus.files["results.failures.json"] as {
      cases: Array<{
        code: string;
        result: { ok: false; error: Record<string, unknown> };
      }>;
    };
    const storageCodes = new Set([
      "state_created_by_newer_version",
      "state_unknown",
      "state_unavailable",
      "state_reset_busy",
      "state_reset_failed",
    ]);
    const storageFailures = failures.cases.filter(({ code }) =>
      storageCodes.has(code),
    );

    expect(storageFailures.map(({ code }) => code)).toEqual([
      "state_created_by_newer_version",
      "state_unknown",
      "state_unavailable",
      "state_reset_busy",
      "state_reset_failed",
    ]);
    for (const { result } of storageFailures) {
      expect(productErrorSchema.safeParse(result.error).success).toBe(true);
      expect(
        productErrorSchema.safeParse({
          ...result.error,
          data: {
            ...(result.error.data as Record<string, unknown>),
            extra: true,
          },
        }).success,
      ).toBe(false);
    }

    const newer = storageFailures[0]?.result.error;
    const newerData = (newer?.data ?? {}) as Record<string, unknown>;
    expect(
      productErrorSchema.safeParse({
        ...newer,
        data: { found_schema: 8, state_directory: "state" },
      }).success,
    ).toBe(false);
    expect(
      productErrorSchema.safeParse({
        ...newer,
        data: { ...newerData, expected_schema: "7" },
      }).success,
    ).toBe(false);
    expect(
      productErrorSchema.safeParse({ ...newer, retryable: true }).success,
    ).toBe(false);
  });
});
