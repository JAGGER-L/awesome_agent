import { Box, Text } from "ink";

import type { ToolItem } from "../../transcript/model.js";
import { ActivityLine } from "../activity/ActivityLine.js";
import { formatActivityDuration } from "../activity/format-duration.js";
import { useTheme } from "../theme.js";

export function ToolSequence({
  items,
  width,
  expanded,
  activityShimmer = false,
}: {
  readonly items: readonly ToolItem[];
  readonly width: number;
  readonly expanded: boolean;
  readonly activityShimmer?: boolean;
}) {
  const theme = useTheme();
  const running = items.findLast((item) => item.outcome === "running");
  const completed = items.filter((item) => item.outcome !== "running");
  const duration = completed.reduce(
    (total, item) => total + (item.duration_ms ?? 0),
    0,
  );
  const summary =
    completed.length > 0 ? (
      <Text color={theme.tool}>
        ● {completed.length} tool {completed.length === 1 ? "call" : "calls"}
        {running ? " completed" : ""} · {formatActivityDuration(duration)} ·
        Ctrl+O
        {expanded ? " to collapse" : " to expand"}
      </Text>
    ) : null;
  return (
    <Box flexDirection="column">
      {summary}
      {expanded ? <ToolDetails items={completed} width={width} /> : null}
      {running ? (
        <ActivityLine
          state="active"
          marker="✦"
          text={`${running.verb}${running.target ? ` ${running.target}` : ""} · Running for`}
          {...(running.started_at === undefined
            ? {}
            : { startedAt: running.started_at })}
          shimmer={activityShimmer}
        />
      ) : null}
    </Box>
  );
}

function ToolDetails({
  items,
  width,
}: {
  readonly items: readonly ToolItem[];
  readonly width: number;
}) {
  const theme = useTheme();
  return items.map((item) => (
    <Box key={item.call_id} flexDirection="column">
      <Text color={item.outcome === "error" ? theme.danger : theme.tool}>
        ● {item.verb}
        {item.target ? ` ${item.target}` : ""}
      </Text>
      <Text color={item.outcome === "error" ? theme.danger : theme.muted}>
        {"  └ "}
        {item.presentation_outcome ?? presentationOutcome(item.outcome)} ·{" "}
        {item.summary}
        {width >= 60 && item.duration_ms !== undefined
          ? ` · ${formatActivityDuration(item.duration_ms)}`
          : ""}
        {item.outcome === "error" && item.error_code
          ? ` · ${item.error_code}`
          : ""}
      </Text>
      {item.detail ? (
        <Text color={theme.muted}>
          {"    "}
          {item.detail.replace(/\n/gu, "\n    ")}
        </Text>
      ) : null}
      {item.detail_truncated_count ? (
        <Text color={theme.muted}>
          {"    "}… +{item.detail_truncated_count} entries
        </Text>
      ) : null}
    </Box>
  ));
}

function presentationOutcome(
  outcome: "running" | "success" | "error" | "cancelled",
): string {
  if (outcome === "running") return "Running";
  if (outcome === "success") return "Completed";
  if (outcome === "error") return "Failed";
  return "Cancelled";
}
