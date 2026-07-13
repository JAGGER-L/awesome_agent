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
        if (sameJsonValue(merged[existingPosition], block)) continue;
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

function sameJsonValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => sameJsonValue(value, right[index]))
    );
  }
  if (!isRecord(left) || !isRecord(right)) return false;
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key, index) =>
        key === rightKeys[index] && sameJsonValue(left[key], right[key]),
    )
  );
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function userStatusRank(
  status: Extract<TranscriptBlock, { kind: "user" }>["status"],
): number {
  return { pending: 0, accepted: 1, failed: 2, persisted: 3 }[status];
}
