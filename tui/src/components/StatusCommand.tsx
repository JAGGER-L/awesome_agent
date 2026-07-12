import { Box, Text } from "ink";

import type { StatusSnapshot } from "../protocol/commands.js";
import { useTheme } from "./theme.js";

export function StatusCommand({
  snapshot,
}: {
  readonly snapshot: StatusSnapshot;
}) {
  const theme = useTheme();
  const operation = snapshot.operation_id
    ? `${snapshot.operation_status} · ${snapshot.operation_id}`
    : snapshot.operation_status;
  const rows = [
    ["Version", snapshot.version],
    ["Workspace", snapshot.workspace_path],
    ["Thread", snapshot.thread_title],
    ["Thread ID", snapshot.thread_display_id],
    ["Provider", snapshot.model_identity.provider],
    [
      "Model",
      `${snapshot.model_identity.effective_model} · ${snapshot.model_status}`,
    ],
    [
      "Fallback",
      snapshot.model_identity.fallback_active
        ? `active · from ${snapshot.model_identity.fallback_from}`
        : "off",
    ],
    [
      "Modes",
      `thinking ${snapshot.thinking_enabled ? "on" : "off"} · skill ${snapshot.skill_mode}`,
    ],
    ["Permissions", snapshot.permission_mode.replace("_", " ")],
    [
      "Memory",
      `local ${snapshot.local_memory_enabled ? "on" : "off"} · mem0 ${snapshot.mem0_enabled ? "on" : "off"}`,
    ],
    ["MCP", `${snapshot.mcp_ready} ready · ${snapshot.mcp_degraded} degraded`],
    ["Operation", operation],
    [
      "Config",
      `${snapshot.configuration_valid ? "valid" : "invalid"} · ${snapshot.configuration_diagnostic_count} diagnostics`,
    ],
  ] as const;
  return (
    <Box flexDirection="column">
      <Text color={theme.accent}>Status</Text>
      <Text> </Text>
      {rows.map(([label, value]) => (
        <Text key={label}>
          {label.padEnd(12)}
          {value}
        </Text>
      ))}
    </Box>
  );
}
