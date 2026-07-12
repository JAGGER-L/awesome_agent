import { Box, Text } from "ink";

import type { TranscriptBlock } from "../../../transcript/model.js";
import { useTheme } from "../../theme.js";

export function BlockView({
  block,
  width,
}: {
  block: TranscriptBlock;
  width: number;
}) {
  const theme = useTheme();
  switch (block.kind) {
    case "user":
      return <Text color={theme.user}>You › {block.text}</Text>;
    case "assistant":
      return <Text color={theme.assistant}>Assistant › {block.text}</Text>;
    case "direct_command":
      return <Text color={theme.accent}>$ {block.command}</Text>;
    case "tools":
      return (
        <Box flexDirection="column">
          {block.items.map((item) => (
            <Text
              key={item.call_id}
              color={item.outcome === "error" ? theme.error : theme.muted}
            >
              {item.outcome === "running"
                ? "…"
                : item.outcome === "success"
                  ? "✓"
                  : "!"}{" "}
              {item.name} · {item.summary}
              {width >= 60 && item.duration_ms > 0
                ? ` · ${item.duration_ms}ms`
                : ""}
              {item.outcome === "error" && item.error_code
                ? ` · ${item.error_code}`
                : ""}
            </Text>
          ))}
        </Box>
      );
    case "change":
      return (
        <Text color={theme.accent}>
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
        <Box flexDirection="column">
          <Text
            color={
              block.tone === "error"
                ? theme.error
                : block.tone === "warning"
                  ? theme.warning
                  : theme.muted
            }
          >
            /{block.command}
          </Text>
          <Text>{block.content}</Text>
        </Box>
      );
    case "error":
      return <Text color={theme.error}>Error · {block.message}</Text>;
    case "omitted_history":
      return <Text dimColor>{block.message}</Text>;
  }
}
