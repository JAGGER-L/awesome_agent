import { Text } from "ink";

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
  if (cancellation.status === "requested") {
    return <Text color={theme.warning}>Cancelling…</Text>;
  }
  if (cancellation.status === "failed") {
    return (
      <Text color={theme.warning}>
        Cancellation failed · {cancellation.message}
      </Text>
    );
  }
  const fullAccess = state.application?.permission_mode === "full_access";
  return (
    <Text bold color={fullAccess ? theme.warning : theme.secondary}>
      {fullAccess ? "◆ Full access" : "◇ Request approval"}
    </Text>
  );
}
