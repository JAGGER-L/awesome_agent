import { Box, Text } from "ink";

import { MarkdownBlock } from "../../markdown/MarkdownBlock.js";
import type { LiveTranscriptProjection } from "../../transcript/model.js";
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
          />
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
