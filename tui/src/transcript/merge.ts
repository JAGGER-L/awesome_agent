import type { TranscriptBlock } from "./model.js";

export function mergeTranscriptBlocks(
  ...groups: readonly (readonly TranscriptBlock[])[]
): readonly TranscriptBlock[] {
  const seen = new Set<string>();
  const userPositions = new Map<string, number>();
  const merged: TranscriptBlock[] = [];
  for (const blocks of groups) {
    for (const block of blocks) {
      if (block.kind === "user") {
        const position = userPositions.get(block.client_message_id);
        if (position !== undefined) {
          const current = merged[position];
          if (
            current?.kind === "user" &&
            userStatusRank(block.status) >= userStatusRank(current.status)
          ) {
            merged[position] = block;
          }
          continue;
        }
        userPositions.set(block.client_message_id, merged.length);
      }
      if (seen.has(block.key)) continue;
      seen.add(block.key);
      merged.push(block);
    }
  }
  return merged;
}

function userStatusRank(
  status: Extract<TranscriptBlock, { kind: "user" }>["status"],
): number {
  return { pending: 0, accepted: 1, failed: 2, persisted: 3 }[status];
}
