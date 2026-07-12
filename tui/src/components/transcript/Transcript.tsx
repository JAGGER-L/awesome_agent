import { Box } from "ink";
import type { TranscriptBlock } from "../../transcript/model.js";
import { BlockView } from "./blocks/BlockView.js";

export function Transcript({
  blocks,
  width,
  toolDetailsExpanded = false,
}: {
  blocks: readonly TranscriptBlock[];
  width: number;
  toolDetailsExpanded?: boolean;
}) {
  return (
    <Box flexDirection="column">
      {blocks.map((block) => (
        <BlockView
          key={block.key}
          block={block}
          width={width}
          toolDetailsExpanded={toolDetailsExpanded}
        />
      ))}
    </Box>
  );
}
