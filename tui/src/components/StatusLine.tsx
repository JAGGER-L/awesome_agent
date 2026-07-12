import { Text } from "ink";

import type { CancellationSnapshot } from "../lifecycle/cancellation.js";
import type { SurfaceState } from "../state/index.js";

export function StatusLine({
  state,
  cancellation = { status: "idle" },
}: {
  state: SurfaceState;
  cancellation?: CancellationSnapshot;
}) {
  if (cancellation.status === "requested") {
    return <Text dimColor>Cancelling…</Text>;
  }
  const operation = state.active_operation?.status ?? "idle";
  return (
    <Text dimColor>
      {state.connection} · {operation} ·{" "}
      {state.application?.permission_mode ?? "request_approval"}
    </Text>
  );
}
