import { Box, Text } from "ink";

import { useTheme } from "../theme.js";
import { formatActivityDuration } from "./format-duration.js";
import { ShimmerText } from "./ShimmerText.js";
import { useElapsedTime } from "./use-elapsed-time.js";

export function ActivityLine({
  state,
  marker,
  text,
  startedAt,
  durationMs,
  shimmer,
  hint,
}: {
  readonly state: "active" | "completed" | "failed" | "cancelled";
  readonly marker: "✦" | "◆" | "✻";
  readonly text: string;
  readonly startedAt?: string;
  readonly durationMs?: number;
  readonly shimmer: boolean;
  readonly hint?: string;
}) {
  const theme = useTheme();
  const active = state === "active";
  const elapsed = useElapsedTime({
    active,
    ...(startedAt === undefined ? {} : { startedAt }),
    ...(durationMs === undefined ? {} : { durationMs }),
  });
  const markerColor =
    state === "failed"
      ? theme.danger
      : state === "cancelled"
        ? theme.warning
        : theme.secondary;
  return (
    <Box>
      <Text color={theme.muted}>│ </Text>
      <Text bold color={markerColor}>
        {marker}{" "}
      </Text>
      <ShimmerText
        text={`${text} ${formatActivityDuration(elapsed)}`}
        active={active && shimmer}
      />
      {hint ? <Text color={theme.muted}> · {hint}</Text> : null}
    </Box>
  );
}
