import { isDeepStrictEqual } from "node:util";

import type { TranscriptBlock } from "./model.js";

export class TranscriptIdentityError extends Error {}

export function mergeTranscriptBlocks(
  ...groups: readonly (readonly TranscriptBlock[])[]
): readonly TranscriptBlock[] {
  const positions = new Map<string, number>();
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
      const existingPosition = positions.get(block.key);
      if (existingPosition !== undefined) {
        if (isDeepStrictEqual(merged[existingPosition], block)) continue;
        throw new TranscriptIdentityError(
          `Transcript key ${block.key} identifies different blocks.`,
        );
      }
      positions.set(block.key, merged.length);
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
