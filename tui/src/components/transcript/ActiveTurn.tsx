import { Box, Text } from "ink";

import type { LiveTranscriptProjection } from "../../transcript/model.js";
import { BlockView } from "./blocks/BlockView.js";

export function ActiveTurn({
  live,
  width,
}: {
  live: LiveTranscriptProjection;
  width: number;
}) {
  if (live.terminal) return null;
  return (
    <Box flexDirection="column">
      {live.reasoning_text ? <Text dimColor>{live.reasoning_text}</Text> : null}
      {live.blocks.map((block) => (
        <BlockView key={block.key} block={block} width={width} />
      ))}
      {width >= 40 && live.usage ? (
        <Text dimColor>
          Tokens {live.usage.input_tokens ?? 0} in ·{" "}
          {live.usage.output_tokens ?? 0} out
        </Text>
      ) : null}
    </Box>
  );
}
