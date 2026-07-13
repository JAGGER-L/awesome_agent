import { describe, expect, it } from "vitest";

import { presentCommandPayload } from "../../src/commands/presenters.js";

const credential = (
  provider: "deepseek" | "kimi" | "mem0",
  selected_source: "environment" | "awesome" | null,
  environment_configured: boolean,
  awesome_configured: boolean,
) => ({
  provider,
  environment_variable: `${provider.toUpperCase()}_API_KEY`,
  environment_configured,
  awesome_configured,
  selected_source,
});

describe("catalog and configuration presenters", () => {
  it("preserves Core tool order and policy facts", () => {
    const result = presentCommandPayload("tools", {
      kind: "tools",
      permission_mode: "request_approval",
      tools: [
        {
          name: "read_file",
          description: "Read",
          read_only: true,
          approval_required: false,
        },
        {
          name: "execute",
          description: "Execute",
          read_only: false,
          approval_required: true,
        },
      ],
    });
    expect(result).toMatchObject({
      kind: "panel",
      rows: [
        { label: "read_file", value: "Enabled", status: "success" },
        { label: "execute", value: "Approval required", status: "warning" },
      ],
    });
  });

  it("renders explicit extension empty states", () => {
    expect(presentCommandPayload("mcp", { kind: "mcp", servers: [] })).toEqual({
      kind: "empty",
      title: "/mcp",
      message: "No MCP servers configured",
    });
  });

  it("shows selected credential sources without exposing secrets", () => {
    const result = presentCommandPayload("config", {
      kind: "config",
      sources: ["defaults", "user"],
      credentials: {
        deepseek: credential("deepseek", "environment", true, false),
        kimi: credential("kimi", "awesome", false, true),
        mem0: credential("mem0", "awesome", false, false),
      },
    });
    expect(result).toMatchObject({
      kind: "panel",
      rows: [
        { label: "Sources", value: "defaults → user" },
        { label: "DeepSeek", value: "Environment" },
        { label: "Kimi", value: "Awesome" },
        { label: "Mem0", value: "Awesome · Unavailable" },
      ],
    });
    expect(JSON.stringify(result)).not.toContain("API_KEY=");
  });
});
