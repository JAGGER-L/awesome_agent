import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { StatusCommand } from "../../src/components/StatusCommand.js";
import {
  statusSnapshotSchema,
  type StatusSnapshot,
} from "../../src/protocol/commands.js";

const snapshot: StatusSnapshot = {
  version: "0.1.0",
  workspace_path: "E:\\projects\\awesome",
  thread_title: "Feature auth",
  thread_id: "thread_3f8a1c2d111122223333444455556666",
  thread_display_id: "thread_3f8a1c2d",
  model_id: "deepseek/deepseek-v4-flash",
  model_status: "configured",
  thinking_enabled: false,
  skill_mode: "auto",
  local_memory_enabled: false,
  mem0_enabled: false,
  mcp_ready: 2,
  mcp_degraded: 0,
  operation_status: "idle",
  operation_id: null,
  configuration_valid: true,
  configuration_diagnostic_count: 0,
  permission_mode: "request_approval",
};

describe("StatusCommand", () => {
  it("accepts the Python idle snapshot with a null operation identity", () => {
    expect(statusSnapshotSchema.parse(snapshot)).toEqual(snapshot);
  });
  it("renders the exact approved status fields", () => {
    const frame =
      render(<StatusCommand snapshot={snapshot} />).lastFrame() ?? "";
    for (const value of [
      "Version     0.1.0",
      "Workspace   E:\\projects\\awesome",
      "Thread      Feature auth",
      "Thread ID   thread_3f8a1c2d",
      "Model       deepseek/deepseek-v4-flash · configured",
      "Modes       thinking off · skill auto",
      "Memory      local off · mem0 off",
      "MCP         2 ready · 0 degraded",
      "Operation   idle",
      "Config      valid · 0 diagnostics",
    ]) {
      expect(frame).toContain(value);
    }
  });

  it("excludes unrelated trust, branch, usage, secrets, health, and dirty state", () => {
    const frame = (
      render(<StatusCommand snapshot={snapshot} />).lastFrame() ?? ""
    ).toLowerCase();
    for (const excluded of [
      "trusted",
      "branch",
      "usage",
      "secret",
      "database",
      "dirty",
      "core version",
      "tui version",
    ]) {
      expect(frame).not.toContain(excluded);
    }
  });

  it("renders active operation identity without inventing detail", () => {
    const frame = render(
      <StatusCommand
        snapshot={{
          ...snapshot,
          operation_status: "active",
          operation_id: "operation_a1b2c3d4",
        }}
      />,
    ).lastFrame();
    expect(frame).toContain("Operation   active · operation_a1b2c3d4");
  });
});
