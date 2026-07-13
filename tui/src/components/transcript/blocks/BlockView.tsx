import { Box, Text } from "ink";

import { MarkdownBlock } from "../../../markdown/MarkdownBlock.js";
import type { TranscriptBlock } from "../../../transcript/model.js";
import { CommandResultView } from "../../CommandResultView.js";
import { useTheme } from "../../theme.js";

export function BlockView({
  block,
  width,
  toolDetailsExpanded = false,
}: {
  block: TranscriptBlock;
  width: number;
  toolDetailsExpanded?: boolean;
}) {
  const theme = useTheme();
  switch (block.kind) {
    case "user":
      return (
        <Box flexDirection="column">
          <Text color={theme.user}>❯ {block.text}</Text>
          {block.status === "failed" ? (
            <Text color={theme.danger}>
              Failed · {block.error_message ?? "Turn was not accepted."}
            </Text>
          ) : null}
        </Box>
      );
    case "command_input":
      return <Text color={theme.user}>鉂?{block.text}</Text>;
    case "assistant":
      return (
        <Box width={width}>
          <Text color={theme.assistant}>● </Text>
          <Box width={Math.max(1, width - 2)}>
            <MarkdownBlock source={block.text} width={Math.max(1, width - 2)} />
          </Box>
        </Box>
      );
    case "direct_command":
      return <Text color={theme.secondary}>$ {block.command}</Text>;
    case "tools":
      if (!toolDetailsExpanded) {
        return (
          <Text color={theme.tool}>
            ● {block.items.length} tool{" "}
            {block.items.length === 1 ? "call" : "calls"} ·{" "}
            {block.items.some((item) => item.outcome === "running")
              ? "Running..."
              : `${block.items.reduce((total, item) => total + (item.duration_ms ?? 0), 0)}ms`}{" "}
            · Ctrl+O to expand
          </Text>
        );
      }
      return (
        <Box flexDirection="column">
          {block.items.map((item) => (
            <Box key={item.call_id} flexDirection="column">
              <Text
                color={item.outcome === "error" ? theme.danger : theme.tool}
              >
                ● {item.verb}
                {item.target ? ` ${item.target}` : ""}
              </Text>
              <Text
                color={item.outcome === "error" ? theme.danger : theme.muted}
              >
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
              {toolDetailsExpanded && item.detail ? (
                <Text color={theme.muted}>
                  {"    "}
                  {item.detail}
                </Text>
              ) : null}
            </Box>
          ))}
        </Box>
      );
    case "change":
      return (
        <Text color={theme.secondary}>
          Changed {block.paths.join(", ")} · {block.reversibility}
        </Text>
      );
    case "reasoning_marker":
      return <Text dimColor>{block.label}</Text>;
    case "warning":
      return <Text color={theme.warning}>Warning · {block.message}</Text>;
    case "status":
      return <Text color={theme.muted}>{block.message}</Text>;
    case "command_result":
      return (
        <CommandResultView presentation={block.presentation} width={width} />
      );
    case "error":
      return <Text color={theme.danger}>Error · {block.message}</Text>;
    case "omitted_history":
      return <Text dimColor>{block.message}</Text>;
  }
}

function presentationOutcome(
  outcome: "success" | "error" | "cancelled",
): string {
  if (outcome === "success") return "Completed";
  if (outcome === "error") return "Failed";
  return "Cancelled";
}
