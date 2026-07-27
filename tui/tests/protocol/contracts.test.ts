import { describe, expect, it } from "vitest";

import { commandOutcomeSchema } from "../../src/protocol/commands.js";
import { productErrorSchema } from "../../src/protocol/base.js";
import { eventEnvelopeSchema } from "../../src/protocol/events.js";
import { methodSchemas } from "../../src/protocol/methods.js";
import {
  applicationStateSchema,
  budgetSchema,
  citationSchema,
  threadEntrySchema,
  usageSummarySchema,
  workspaceInstructionDiagnosticSchema,
} from "../../src/protocol/product-projections.js";
import { modelCatalogSchema } from "../../src/protocol/model-catalog.js";
import { PRODUCT_VERSION } from "../../src/version.js";
import { loadFixtureCorpus } from "../contracts/fixture-loader.js";
import { freshModelCatalog } from "../fixtures/model-catalog.js";

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

describe("protocol v4 Skill package management contracts", () => {
  it("accepts strict pre-initialize Skill requests and closed mutation results", () => {
    expect(methodSchemas["skill.list"].params.safeParse({}).success).toBe(true);
    expect(
      methodSchemas["skill.install"].params.safeParse({
        source_path: "./review.zip",
        replace: true,
      }).success,
    ).toBe(true);
    expect(
      methodSchemas["skill.install"].params.safeParse({
        source_path: "./review.zip",
      }).success,
    ).toBe(true);
    expect(
      methodSchemas["skill.remove"].params.safeParse({ name: "review" })
        .success,
    ).toBe(true);
    expect(
      methodSchemas["skill.install"].value.safeParse({
        name: "review",
        status: "replaced",
      }).success,
    ).toBe(true);
    expect(
      methodSchemas["skill.remove"].value.safeParse({
        name: "review",
        status: "removed",
      }).success,
    ).toBe(true);
    expect(
      methodSchemas["skill.remove"].value.safeParse({
        name: "review",
        status: "deleted",
      }).success,
    ).toBe(false);
  });

  it("rejects unsafe paths, malformed names, unknown fields, and unordered lists", () => {
    const install = methodSchemas["skill.install"].params;
    expect(
      install.safeParse({ source_path: " review.zip ", replace: false })
        .success,
    ).toBe(false);
    expect(
      install.safeParse({ source_path: "review\n.zip", replace: false })
        .success,
    ).toBe(false);
    expect(
      install.safeParse({
        source_path: "review.zip",
        replace: false,
        validate_package: true,
      }).success,
    ).toBe(false);
    expect(
      methodSchemas["skill.remove"].params.safeParse({ name: "Review_Skill" })
        .success,
    ).toBe(false);

    const list = methodSchemas["skill.list"].value;
    const ordered = {
      skills: [
        { name: "alpha", description: "Alpha workflow" },
        { name: "review", description: "Review workflow" },
      ],
    };
    expect(list.safeParse(ordered).success).toBe(true);
    expect(
      list.safeParse({ skills: [...ordered.skills].reverse() }).success,
    ).toBe(false);
    expect(
      list.safeParse({ skills: [ordered.skills[0], ordered.skills[0]] })
        .success,
    ).toBe(false);
    expect(
      list.safeParse({
        skills: Array.from({ length: 513 }, (_, index) => ({
          name: `skill-${String(index).padStart(3, "0")}`,
          description: "Bounded Skill",
        })),
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

describe("thread search and export protocol", () => {
  it("trims and bounds search params while applying the default page size", () => {
    expect(
      methodSchemas["thread.search"].params.parse({ query: "  retry loop  " }),
    ).toEqual({ query: "retry loop", limit: 50 });
    expect(
      methodSchemas["thread.search"].params.safeParse({ query: "   " }).success,
    ).toBe(false);
    expect(
      methodSchemas["thread.search"].params.safeParse({
        query: "x".repeat(201),
      }).success,
    ).toBe(false);
    expect(
      methodSchemas["thread.search"].params.safeParse({
        query: "retry",
        limit: 51,
      }).success,
    ).toBe(false);
    expect(
      methodSchemas["thread.search"].params.safeParse({
        query: "retry",
        cursor: "x".repeat(1_025),
      }).success,
    ).toBe(false);
  });

  it("requires change sets exactly when an export writes bytes", () => {
    const changed = {
      kind: "result",
      payload: {
        kind: "thread_export",
        thread_id: "thread_1",
        path: "exports/thread.md",
        format: "markdown",
        write_status: "created",
        byte_count: 128,
        change_set_id: "change_1",
      },
    } as const;
    expect(commandOutcomeSchema.safeParse(changed).success).toBe(true);
    expect(
      commandOutcomeSchema.safeParse({
        ...changed,
        payload: { ...changed.payload, path: "x".repeat(1_001) },
      }).success,
    ).toBe(false);
    expect(
      commandOutcomeSchema.safeParse({
        ...changed,
        payload: { ...changed.payload, change_set_id: "" },
      }).success,
    ).toBe(false);
    expect(
      commandOutcomeSchema.safeParse({
        ...changed,
        payload: { ...changed.payload, change_set_id: undefined },
      }).success,
    ).toBe(false);
    expect(
      commandOutcomeSchema.safeParse({
        ...changed,
        payload: {
          ...changed.payload,
          write_status: "unchanged",
          change_set_id: "change_1",
        },
      }).success,
    ).toBe(false);
    expect(
      commandOutcomeSchema.safeParse({
        ...changed,
        payload: {
          ...changed.payload,
          write_status: "unchanged",
          change_set_id: undefined,
        },
      }).success,
    ).toBe(true);
  });
});

describe("application Model Catalog protocol", () => {
  it("requires the catalog on every ApplicationState", () => {
    const { model_catalog: _catalog, ...withoutCatalog } = applicationState();
    void _catalog;

    expect(applicationStateSchema.safeParse(withoutCatalog).success).toBe(
      false,
    );
  });

  it("rejects duplicate Provider and model identifiers", () => {
    const duplicateProvider = freshModelCatalog();
    duplicateProvider.providers.push(
      structuredClone(catalogProvider(duplicateProvider, 0)),
    );

    const duplicateModel = freshModelCatalog();
    catalogProvider(duplicateModel, 0).models.push(
      structuredClone(catalogModel(duplicateModel, 0, 0)),
    );

    expect(modelCatalogSchema.safeParse(duplicateProvider).success).toBe(false);
    expect(modelCatalogSchema.safeParse(duplicateModel).success).toBe(false);
  });

  it("rejects models whose Provider prefix or default declaration is invalid", () => {
    const wrongPrefix = freshModelCatalog();
    catalogModel(wrongPrefix, 0, 0).id = "kimi/deepseek-v4-flash";

    const noDefault = freshModelCatalog();
    for (const model of catalogProvider(noDefault, 0).models) {
      model.is_default = false;
    }

    const multipleDefaults = freshModelCatalog();
    for (const model of catalogProvider(multipleDefaults, 0).models) {
      model.is_default = true;
    }

    expect(modelCatalogSchema.safeParse(wrongPrefix).success).toBe(false);
    expect(modelCatalogSchema.safeParse(noDefault).success).toBe(false);
    expect(modelCatalogSchema.safeParse(multipleDefaults).success).toBe(false);
  });

  it("rejects duplicate, missing, unsupported, and extraneous region defaults", () => {
    const duplicateRegion = freshModelCatalog();
    catalogProvider(duplicateRegion, 1).supported_regions = ["cn", "cn"];

    const missingDefault = freshModelCatalog();
    delete catalogProvider(missingDefault, 1).default_region;

    const unsupportedDefault = freshModelCatalog();
    catalogProvider(unsupportedDefault, 1).supported_regions = ["cn"];
    catalogProvider(unsupportedDefault, 1).default_region = "global";

    const extraneousDefault = freshModelCatalog();
    catalogProvider(extraneousDefault, 0).default_region = "cn";

    for (const catalog of [
      duplicateRegion,
      missingDefault,
      unsupportedDefault,
      extraneousDefault,
    ]) {
      expect(modelCatalogSchema.safeParse(catalog).success).toBe(false);
    }
  });

  it("requires every catalog credential association to be published", () => {
    const application = applicationState();
    catalogProvider(application.model_catalog, 0).credential_id = "orphan";

    expect(applicationStateSchema.safeParse(application).success).toBe(false);
  });

  it("rejects model identities outside the published catalog", () => {
    const unknownProvider = applicationState();
    requiredModelIdentity(unknownProvider).provider = "future";

    const unknownConfiguredModel = applicationState();
    requiredModelIdentity(unknownConfiguredModel).configured_model =
      "deepseek/not-published";

    const unknownEffectiveModel = applicationState();
    const effectiveIdentity = requiredModelIdentity(unknownEffectiveModel);
    effectiveIdentity.effective_model = "deepseek/not-published";
    effectiveIdentity.fallback_active = true;
    effectiveIdentity.fallback_from = "deepseek/deepseek-v4-flash";

    for (const application of [
      unknownProvider,
      unknownConfiguredModel,
      unknownEffectiveModel,
    ]) {
      expect(applicationStateSchema.safeParse(application).success).toBe(false);
    }
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

function applicationState() {
  return applicationStateSchema.parse({
    initialized: true,
    session_id: "session_1",
    workspace_key: "workspace_1",
    workspace: { display_path: "E:\\workspace" },
    workspace_trusted: true,
    model_catalog: freshModelCatalog(),
    model_identity: {
      provider: "deepseek",
      configured_model: "deepseek/deepseek-v4-flash",
      effective_model: "deepseek/deepseek-v4-flash",
      runtime_name: "Awesome Agent",
      fallback_active: false,
    },
    thinking_enabled: false,
    skill_mode: "auto",
    permission_mode: "request_approval",
    configuration_valid: true,
    secret_status: {
      deepseek_api_key: true,
      moonshot_api_key: false,
      mem0_api_key: false,
    },
    provider_credentials: {
      deepseek: {
        provider: "deepseek",
        environment_variable: "DEEPSEEK_API_KEY",
        environment_configured: true,
        awesome_configured: false,
        selected_source: "environment",
      },
      kimi: {
        provider: "kimi",
        environment_variable: "MOONSHOT_API_KEY",
        environment_configured: false,
        awesome_configured: false,
        selected_source: null,
      },
      mem0: {
        provider: "mem0",
        environment_variable: "MEM0_API_KEY",
        environment_configured: false,
        awesome_configured: false,
        selected_source: null,
      },
    },
    memory_status: {},
    mcp_status: [],
    usage: {},
    configuration_diagnostics: [],
  });
}

function catalogProvider(
  catalog: ReturnType<typeof freshModelCatalog>,
  index: number,
) {
  const provider = catalog.providers[index];
  if (!provider) throw new Error(`Provider fixture ${index} is missing`);
  return provider;
}

function catalogModel(
  catalog: ReturnType<typeof freshModelCatalog>,
  providerIndex: number,
  modelIndex: number,
) {
  const model = catalogProvider(catalog, providerIndex).models[modelIndex];
  if (!model) throw new Error(`Model fixture ${modelIndex} is missing`);
  return model;
}

function requiredModelIdentity(
  application: ReturnType<typeof applicationState>,
) {
  const identity = application.model_identity;
  if (!identity) throw new Error("Model identity fixture is missing");
  return identity;
}
