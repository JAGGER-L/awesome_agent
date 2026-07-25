import { Box, Text } from "ink";

import type { CancellationSnapshot } from "../lifecycle/cancellation.js";
import type { SurfaceState } from "../state/index.js";
import { useTheme } from "./theme.js";

export function StatusLine({
  state,
  cancellation = { status: "idle" },
}: {
  state: SurfaceState;
  cancellation?: CancellationSnapshot;
}) {
  const theme = useTheme();
  const diagnostic = state.application?.workspace_instruction_diagnostic;
  const warning = diagnostic ? (
    <Text color={theme.warning}>⚠ {diagnostic.message}</Text>
  ) : null;
  if (cancellation.status === "requested") {
    return (
      <Box flexDirection="column">
        {warning}
        <Text color={theme.warning}>Cancelling…</Text>
      </Box>
    );
  }
  if (cancellation.status === "failed") {
    return (
      <Box flexDirection="column">
        {warning}
        <Text color={theme.warning}>
          Cancellation failed · {cancellation.message}
        </Text>
      </Box>
    );
  }
  const permissionMode =
    state.application?.permission_mode ?? "request_approval";
  const fullAccess = permissionMode === "full_access";
  const permissionLabel =
    permissionMode === "full_access"
      ? "◆ Full access"
      : permissionMode === "accept_edits"
        ? "◈ Accept edits"
        : "◇ Request approval";
  return (
    <Box flexDirection="column">
      {warning}
      <Text bold color={fullAccess ? theme.warning : theme.secondary}>
        {permissionLabel}
      </Text>
    </Box>
  );
}
