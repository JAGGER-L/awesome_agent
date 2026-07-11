import type { TranscriptBlock } from "./model.js";

export function mergeTranscriptBlocks(
  ...groups: readonly (readonly TranscriptBlock[])[]
): readonly TranscriptBlock[] {
  const seen = new Set<string>();
  const merged: TranscriptBlock[] = [];
  for (const blocks of groups) {
    for (const block of blocks) {
      if (seen.has(block.key)) continue;
      seen.add(block.key);
      merged.push(block);
    }
  }
  return merged;
}
