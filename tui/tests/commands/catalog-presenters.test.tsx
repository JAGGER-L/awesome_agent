import { describe, expect, it } from "vitest";

import { presentCommandPayload } from "../../src/commands/presenters.js";
import { findCommand } from "../../src/commands/catalog.js";

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
  it("registers canonical rename completion without a placeholder", () => {
    expect(findCommand("rename")).toMatchObject({
      completion: "/rename",
      usage: "/rename <title>",
    });
  });

  it("presents a successful rename as one success notice", () => {
    const result = presentCommandPayload("rename", {
      kind: "thread_renamed",
      thread: {
        id: "thread_1",
        workspace_key: "workspace_1",
        title: "Cube helper",
        title_source: "manual",
        current_model: "deepseek/deepseek-v4-flash",
        thinking_enabled: true,
        skill_mode: "auto",
        lineage: null,
        created_at: "2026-07-14T00:00:00Z",
        updated_at: "2026-07-14T00:00:01Z",
      },
    });

    expect(result).toEqual({
      kind: "notice",
      message: "Conversation renamed · Cube helper",
      tone: "success",
    });
  });

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
