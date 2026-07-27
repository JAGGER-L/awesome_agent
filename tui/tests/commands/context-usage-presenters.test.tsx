import { describe, expect, it } from "vitest";

import { presentCommandPayload } from "../../src/commands/presenters.js";

describe("context and usage presenters", () => {
  it("uses the canonical context order and binary token units", () => {
    const result = presentCommandPayload("context", {
      kind: "context",
      categories: [
        { name: "files", estimated_tokens: 1_229 },
        { name: "memory", estimated_tokens: 640 },
        { name: "conversation", estimated_tokens: 4_608 },
        { name: "instructions", estimated_tokens: 12_288 },
      ],
      total_tokens: 18_739,
      budget_tokens: 262_144,
    });
    expect(result).toMatchObject({
      kind: "panel",
      rows: [
        { label: "Instructions", value: "12K" },
        { label: "Conversation", value: "4.5K" },
        { label: "Files", value: "1.2K" },
        { label: "Memory", value: "640" },
        { label: "Total", value: "18.3K" },
        { label: "Budget", value: "256K" },
      ],
    });
  });

  it("shows every usage metric on its own row", () => {
    const result = presentCommandPayload("usage", {
      kind: "usage",
      usage: {
        input_tokens: 1024,
        output_tokens: 12,
        reasoning_tokens: 4,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        model_calls: 2,
        tool_calls: 3,
        provider_retries: 1,
        compressions: 1,
        web_requests: 3,
        active_execution_seconds: 2.2,
      },
    });
    expect(result.kind).toBe("panel");
    if (result.kind !== "panel") return;
    expect("valueAlignment" in result).toBe(false);
    expect(result.rows.map((row) => row.label)).toEqual([
      "Input tokens",
      "Output tokens",
      "Reasoning tokens",
      "Cache read tokens",
      "Cache write tokens",
      "Model calls",
      "Tool calls",
      "Provider retries",
      "Compressions",
      "Web requests",
      "Active execution",
    ]);
    expect(result.rows.map((row) => row.value)).toEqual([
      "1K",
      "12",
      "4",
      "0",
      "0",
      "2",
      "3",
      "1",
      "1",
      "3",
      "2.2s",
    ]);
  });

  it("presents Web availability, authority, and disclosure together", () => {
    const result = presentCommandPayload("web", {
      kind: "web_status",
      enabled: true,
      provider: "tavily",
      available: false,
      credential_configured: false,
      proxy_configured: true,
      thread_authorized: false,
      requests_per_turn: 8,
      diagnostic_code: "tavily_credential_missing",
      disclosure: "Queries and URLs are sent to Tavily.",
    });

    expect(result).toMatchObject({ kind: "panel", title: "/web" });
    if (result.kind !== "panel") return;
    expect(result.rows).toEqual(
      expect.arrayContaining([
        { label: "Available", value: "No", status: "warning" },
        { label: "Thread access", value: "Approval required" },
        {
          label: "Diagnostic",
          value: "tavily_credential_missing",
          status: "warning",
        },
        { label: "Disclosure", value: "Queries and URLs are sent to Tavily." },
      ]),
    );
  });
});
