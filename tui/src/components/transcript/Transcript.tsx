import { Static } from "ink";

import type { TranscriptBlock } from "../../transcript/model.js";
import { BlockView } from "./blocks/BlockView.js";

export function Transcript({
  blocks,
  width,
}: {
  blocks: readonly TranscriptBlock[];
  width: number;
}) {
  return (
    <Static items={[...blocks]}>
      {(block) => <BlockView key={block.key} block={block} width={width} />}
    </Static>
  );
}
