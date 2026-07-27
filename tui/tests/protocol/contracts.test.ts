import { describe, expect, it } from "vitest";

import { commandOutcomeSchema } from "../../src/protocol/commands.js";
import { productErrorSchema } from "../../src/protocol/base.js";
import { eventEnvelopeSchema } from "../../src/protocol/events.js";
import { methodSchemas } from "../../src/protocol/methods.js";
import {
  budgetSchema,
  citationSchema,
  threadEntrySchema,
  usageSummarySchema,
  workspaceInstructionDiagnosticSchema,
} from "../../src/protocol/product-projections.js";
import { PRODUCT_VERSION } from "../../src/version.js";
import { loadFixtureCorpus } from "../contracts/fixture-loader.js";

describe("protocol v4 handshake", () => {
  const params = {
    protocol_version: 4,
    client_name: "awesome",
    client_version: PRODUCT_VERSION,
  } as const;
  const value = {
    product_version: PRODUCT_VERSION,
    protocol_version: 4,
    status: "ready",
    session_id: "session_11111111111111111111111111111111",
    workspace: { display_path: "C:\\workspace" },
    capabilities: ["threads", "turns", "commands", "web", "citations"],
  } as const;

  it("accepts v4 and rejects old v3 values in both handshake directions", () => {
    expect(methodSchemas.initialize.params.safeParse(params).success).toBe(
      true,
    );
    expect(
      methodSchemas.initialize.params.safeParse({
        ...params,
        protocol_version: 3,
      }).success,
    ).toBe(false);
    expect(methodSchemas.initialize.value.safeParse(value).success).toBe(true);
    expect(
      methodSchemas.initialize.value.safeParse({
        ...value,
        protocol_version: 3,
      }).success,
    ).toBe(false);
    expect(
      methodSchemas.initialize.value.safeParse({
        ...value,
        capabilities: ["threads", "turns", "commands", "web"],
      }).success,
    ).toBe(false);
  });
});

describe("protocol v4 Web and citation contracts", () => {
  const citation = {
    id: "S1",
    title: "Primary source",
    url: "https://example.com/source",
  } as const;
  const entry = {
    id: "entry_1",
    thread_id: "thread_1",
    sequence: 1,
    kind: "assistant_message",
    content: "Answer [[S1]]",
    metadata: { citations: [citation] },
    created_at: "2026-07-27T00:00:00Z",
  } as const;

  it("validates strict assistant metadata and absolute HTTPS citations", () => {
    expect(citationSchema.safeParse(citation).success).toBe(true);
    expect(threadEntrySchema.safeParse(entry).success).toBe(true);
    expect(
      threadEntrySchema.safeParse({ ...entry, metadata: {} }).success,
    ).toBe(false);
    expect(
      threadEntrySchema.safeParse({
        ...entry,
        metadata: { citations: [citation], private_query: "secret" },
      }).success,
    ).toBe(false);
    expect(
      citationSchema.safeParse({ ...citation, url: "http://example.com" })
        .success,
    ).toBe(false);
    expect(
      citationSchema.safeParse({ ...citation, url: "https://user@example.com" })
        .success,
    ).toBe(false);
    expect(
      threadEntrySchema.safeParse({
        ...entry,
        metadata: { citations: [{ ...citation, id: "S2" }] },
      }).success,
    ).toBe(false);
    expect(
      threadEntrySchema.safeParse({
        ...entry,
        metadata: {
          citations: [citation, { ...citation, id: "S2", title: "Duplicate" }],
        },
      }).success,
    ).toBe(false);
  });

  it("bounds Web budgets while allowing cumulative usage", () => {
    const budget = {
      model_calls: 32,
      tool_calls: 64,
      provider_retries: 2,
      compressions: 2,
      active_execution_seconds: 1_800,
      total_context_tokens: 262_144,
      web_requests: 8,
    };
    expect(budgetSchema.safeParse(budget).success).toBe(true);
    expect(budgetSchema.safeParse({ ...budget, web_requests: 9 }).success).toBe(
      false,
    );
    expect(
      usageSummarySchema.safeParse({
        input_tokens: 0,
        output_tokens: 0,
        reasoning_tokens: 0,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        model_calls: 0,
        tool_calls: 0,
        provider_retries: 0,
        compressions: 0,
        web_requests: 10,
        active_execution_seconds: 0,
      }).success,
    ).toBe(true);
  });

  it("accepts the exact /web status payload and rejects unknown fields", () => {
    const outcome = {
      kind: "result",
      payload: {
        kind: "web_status",
        enabled: true,
        provider: "tavily",
        available: true,
        credential_configured: true,
        proxy_configured: false,
        thread_authorized: false,
        requests_per_turn: 8,
        disclosure: "Queries and URLs are sent to Tavily.",
      },
    } as const;
    expect(commandOutcomeSchema.safeParse(outcome).success).toBe(true);
    expect(
      commandOutcomeSchema.safeParse({
        ...outcome,
        payload: { ...outcome.payload, query: "private" },
      }).success,
    ).toBe(false);
  });

  it("requires deny-first network choices and envelope-bound identities", () => {
    const event = {
      version: 1,
      event_id: "event_network1",
      sequence: 1,
      session_id: "session_1",
      workspace_key: "workspace_1",
      thread_id: "thread_1",
      turn_id: "turn_1",
      operation_id: "operation_1",
      event_type: "interaction.required",
      timestamp: "2026-07-27T00:00:00Z",
      payload: {
        kind: "interaction.required",
        interaction_id: "interaction_1",
        interaction_kind: "tool_approval",
        prompt: "Send this query to Tavily?",
        operation: "web_search",
        target: "Tavily",
        capability: "network.read",
        choices: [
          { decision: "deny", label: "Deny" },
          { decision: "allow_once", label: "Allow once" },
          {
            decision: "allow_thread_network",
            label: "Allow for this Thread",
          },
        ],
      },
    } as const;
    expect(eventEnvelopeSchema.safeParse(event).success).toBe(true);
    expect(
      eventEnvelopeSchema.safeParse({
        ...event,
        payload: {
          ...event.payload,
          choices: [...event.payload.choices].reverse(),
        },
      }).success,
    ).toBe(false);
    const { turn_id: _turnId, ...missingTurn } = event;
    void _turnId;
    expect(eventEnvelopeSchema.safeParse(missingTurn).success).toBe(false);
  });
});

describe("workspace instruction diagnostic protocol", () => {
  const diagnostic = {
    code: "workspace_instructions_too_large",
    source_id: "AGENTS.md",
    message: "AGENTS.md was ignored because it is too large.",
  } as const;

  it("rejects unknown diagnostic codes and sources", () => {
    expect(
      workspaceInstructionDiagnosticSchema.safeParse(diagnostic).success,
    ).toBe(true);
    expect(
      workspaceInstructionDiagnosticSchema.safeParse({
        ...diagnostic,
        code: "workspace_instructions_future",
      }).success,
    ).toBe(false);
    expect(
      workspaceInstructionDiagnosticSchema.safeParse({
        ...diagnostic,
        source_id: "PROJECT.md",
      }).success,
    ).toBe(false);
  });
});

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
    if (!result.ok) return;
    const assistant = result.value.view.entries.find(
      (entry) => entry.kind === "assistant_message",
    );
    expect(assistant?.metadata.citations).toEqual([
      {
        id: "S1",
        title: "Fixture source",
        url: "https://example.com/source",
      },
    ]);
    expect(result.value.view.turns[0]?.budgets.web_requests).toBe(8);
    expect(result.value.view.turns[0]?.usage.web_requests).toBe(1);
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
        capabilities: ["threads", "turns", "commands", "web", "citations"],
        interaction_id: "interaction_state_reset",
        product_version: PRODUCT_VERSION,
        protocol_version: 4,
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
