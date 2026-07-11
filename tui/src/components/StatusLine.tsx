import { Text } from "ink";

import type { SurfaceState } from "../state/index.js";

export function StatusLine({ state }: { state: SurfaceState }) {
  const operation = state.active_operation?.status ?? "idle";
  return (
    <Text dimColor>
      {state.connection} · {operation}
    </Text>
  );
}
