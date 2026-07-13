import { Box, Text } from "ink";

import { MarkdownBlock } from "../../markdown/MarkdownBlock.js";
import type { LiveTranscriptProjection } from "../../transcript/model.js";
import { ActivityLine } from "../activity/ActivityLine.js";
import { useTheme } from "../theme.js";
import { BlockView } from "./blocks/BlockView.js";

export function ActiveTurn({
  live,
  width,
  detailsExpanded = false,
}: {
  live: LiveTranscriptProjection;
  width: number;
  detailsExpanded?: boolean;
}) {
  const theme = useTheme();
  const activeThinking = live.blocks.findLast(
    (block) => block.kind === "thinking" && block.duration_ms === undefined,
  );
  const activeTools = live.blocks.findLast(
    (block) =>
      block.kind === "tools" &&
      block.items.some((item) => item.outcome === "running"),
  );
  const specificActivityKey = activeThinking?.key ?? activeTools?.key;
  return (
    <Box flexDirection="column">
      {live.blocks.map((block) =>
        block.kind === "assistant" ? (
          <Box key={block.key} width={width}>
            <Text color={theme.assistant}>● </Text>
            <Box width={Math.max(1, width - 2)}>
              <MarkdownBlock
                source={block.text}
                width={Math.max(1, width - 2)}
              />
            </Box>
          </Box>
        ) : (
          <BlockView
            key={block.key}
            block={block}
            width={width}
            detailsExpanded={detailsExpanded}
            activityShimmer={block.key === specificActivityKey}
          />
        ),
      )}
      {width >= 60 && live.usage ? (
        <Text dimColor>
          Tokens {live.usage.input_tokens ?? 0} in ·{" "}
          {live.usage.output_tokens ?? 0} out
        </Text>
      ) : null}
      {!live.terminal && live.started_at ? (
        <ActivityLine
          state="active"
          marker="✦"
          text="Working for"
          startedAt={live.started_at}
          shimmer={specificActivityKey === undefined}
        />
      ) : null}
    </Box>
  );
}
