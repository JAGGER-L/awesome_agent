import { describe, expect, it } from "vitest";

import { presentCommandPayload } from "../../src/commands/presenters.js";

describe("workspace, status, and doctor presenters", () => {
  it("shows only the normalized workspace path", () => {
    expect(
      presentCommandPayload("workspace", {
        kind: "workspace",
        path: "E:/awesome_agent",
      }),
    ).toEqual({ kind: "notice", message: "E:/awesome_agent", tone: "info" });
  });

  it("shows the canonical user-facing status rows", () => {
    const result = presentCommandPayload("status", {
      kind: "status",
      snapshot: {
        version: "1.2.1",
        workspace_path: "E:/awesome_agent",
        thread_title: "Architecture review",
        thread_id: "thread_12345678",
        thread_display_id: "12345678",
        model_identity: {
          provider: "deepseek",
          configured_model: "deepseek-chat",
          effective_model: "deepseek-chat",
          runtime_name: "Awesome Agent",
          fallback_active: false,
        },
        model_status: "configured",
        thinking_enabled: false,
        skill_mode: "auto",
        local_memory_enabled: true,
        mem0_enabled: false,
        mcp_ready: 1,
        mcp_degraded: 0,
        operation_status: "idle",
        configuration_valid: true,
        configuration_diagnostic_count: 0,
        permission_mode: "request_approval",
        credential_source: "awesome",
        credential_source_available: true,
        context_used_tokens: 18_204,
        context_budget_tokens: 262_144,
        changed_file_count: 1,
      },
    });
    expect(result.kind).toBe("panel");
    if (result.kind !== "panel") return;
    expect("valueAlignment" in result).toBe(false);
    expect(result.rows.map((row) => row.label)).toEqual([
      "Version",
      "Workspace",
      "Thread",
      "Model",
      "Credentials",
      "Permissions",
      "Context",
      "Thinking",
      "Skill",
      "Memory",
      "MCP",
      "Operation",
      "Changes",
    ]);
    expect(result.rows.find((row) => row.label === "Credentials")?.value).toBe(
      "Awesome",
    );
    expect(result.rows.find((row) => row.label === "Permissions")?.value).toBe(
      "Request approval",
    );
    expect(result.rows.find((row) => row.label === "Changes")?.value).toBe(
      "1 file modified",
    );
  });

  it("maps doctor states without leaking diagnostic detail into the status column", () => {
    const result = presentCommandPayload("doctor", {
      kind: "doctor",
      checks: [
        { name: "Python", status: "ok", detail: "3.12.10" },
        { name: "DeepSeek", status: "missing", detail: "DEEPSEEK_API_KEY" },
        {
          name: "Configuration",
          status: "invalid",
          detail: "private diagnostic",
        },
      ],
    });
    expect(result).toMatchObject({
      kind: "panel",
      rows: [
        { label: "Python", value: "OK" },
        { label: "DeepSeek", value: "Missing" },
        { label: "Configuration", value: "Invalid", status: "danger" },
      ],
    });
  });
});
