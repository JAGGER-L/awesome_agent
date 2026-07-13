import { Box, Text } from "ink";

import { MarkdownBlock } from "../../../markdown/MarkdownBlock.js";
import type { TranscriptBlock } from "../../../transcript/model.js";
import { CommandResultView } from "../../CommandResultView.js";
import { useTheme } from "../../theme.js";
import { formatDuration } from "../../../transcript/reasoning.js";
import { ToolSequence } from "../ToolSequence.js";
import { Worked } from "../Worked.js";

export function BlockView({
  block,
  width,
  detailsExpanded = false,
}: {
  block: TranscriptBlock;
  width: number;
  detailsExpanded?: boolean;
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
      return (
        <ToolSequence
          items={block.items}
          width={width}
          expanded={detailsExpanded}
        />
      );
    case "change":
      return (
        <Text color={theme.secondary}>
          Changed {block.paths.join(", ")} · {block.reversibility}
        </Text>
      );
    case "thinking":
      if (block.duration_ms === undefined)
        return (
          <Box flexDirection="column">
            <Text dimColor>Thinking...</Text>
            <Text dimColor>{block.text}</Text>
          </Box>
        );
      return (
        <Box flexDirection="column">
          <Text dimColor>
            Thought for {formatDuration(block.duration_ms)} · Ctrl+O to expand
          </Text>
          {detailsExpanded ? <Text dimColor>{block.text}</Text> : null}
        </Box>
      );
    case "worked":
      return <Worked durationMs={block.duration_ms} />;
    case "warning":
      return <Text color={theme.warning}>Warning · {block.message}</Text>;
    case "status":
      return <Text color={theme.muted}>{block.message}</Text>;
    case "command_result":
      return (
        <CommandResultView
          presentation={block.presentation}
          width={width}
          detailsExpanded={detailsExpanded}
        />
      );
    case "error":
      return <Text color={theme.danger}>Error · {block.message}</Text>;
    case "omitted_history":
      return <Text dimColor>{block.message}</Text>;
  }
}
