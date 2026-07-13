import { Box, Text } from "ink";

import type { ToolItem } from "../../transcript/model.js";
import { useTheme } from "../theme.js";

export function ToolSequence({
  items,
  width,
  expanded,
}: {
  readonly items: readonly ToolItem[];
  readonly width: number;
  readonly expanded: boolean;
}) {
  const theme = useTheme();
  if (!expanded) {
    const running = items.some((item) => item.outcome === "running");
    const durationsKnown = items.every(
      (item) => item.outcome === "running" || item.duration_ms !== undefined,
    );
    const duration = items.reduce(
      (total, item) => total + (item.duration_ms ?? 0),
      0,
    );
    return (
      <Text color={theme.tool}>
        ● {items.length} tool {items.length === 1 ? "call" : "calls"} ·{" "}
        {running
          ? "Running..."
          : durationsKnown
            ? `${duration}ms`
            : "Completed"}{" "}
        · Ctrl+O to expand
      </Text>
    );
  }
  return (
    <Box flexDirection="column">
      {items.map((item) => (
        <Box key={item.call_id} flexDirection="column">
          <Text color={item.outcome === "error" ? theme.danger : theme.tool}>
            ● {item.verb}
            {item.target ? ` ${item.target}` : ""}
          </Text>
          <Text color={item.outcome === "error" ? theme.danger : theme.muted}>
            {"  └ "}
            {item.outcome === "running"
              ? item.summary
              : `${item.presentation_outcome ?? presentationOutcome(item.outcome)} · ${item.summary}`}
            {width >= 60 && item.duration_ms !== undefined
              ? ` · ${item.duration_ms}ms`
              : ""}
            {item.outcome === "error" && item.error_code
              ? ` · ${item.error_code}`
              : ""}
          </Text>
          {item.detail
            ? item.detail.split("\n").map((line, index) => (
                <Text
                  key={`${item.call_id}:detail:${index}`}
                  color={theme.muted}
                >
                  {"    "}
                  {line}
                </Text>
              ))
            : null}
          {item.detail_truncated_count ? (
            <Text color={theme.muted}>
              {"    "}… +{item.detail_truncated_count} entries
            </Text>
          ) : null}
        </Box>
      ))}
    </Box>
  );
}

function presentationOutcome(
  outcome: "success" | "error" | "cancelled",
): string {
  if (outcome === "success") return "Completed";
  if (outcome === "error") return "Failed";
  return "Cancelled";
}
