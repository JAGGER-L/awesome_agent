import { Box, Text } from "ink";

import { formatStreamingMarkdown } from "../../markdown/streaming.js";
import type { LiveTranscriptProjection } from "../../transcript/model.js";
import { useTheme } from "../theme.js";
import { BlockView } from "./blocks/BlockView.js";

export function ActiveTurn({
  live,
  width,
}: {
  live: LiveTranscriptProjection;
  width: number;
}) {
  const theme = useTheme();
  if (live.terminal) return null;
  return (
    <Box flexDirection="column">
      {live.reasoning_text ? <Text dimColor>{live.reasoning_text}</Text> : null}
      {live.blocks.map((block) =>
        block.kind === "assistant" ? (
          <Box key={block.key} width={width}>
            <Text color={theme.assistant}>● </Text>
            <Text wrap="wrap">{formatStreamingMarkdown(block.text)}</Text>
          </Box>
        ) : (
          <BlockView key={block.key} block={block} width={width} />
        ),
      )}
      {width >= 60 && live.usage ? (
        <Text dimColor>
          Tokens {live.usage.input_tokens ?? 0} in ·{" "}
          {live.usage.output_tokens ?? 0} out
        </Text>
      ) : null}
    </Box>
  );
}
