import { randomUUID } from "node:crypto";

export const MAX_PENDING_INPUTS = 3;

export interface PendingInput {
  readonly id: string;
  readonly raw: string;
  readonly clientMessageId?: string;
  readonly terminalBarrier: boolean;
}

export interface PendingInputState {
  readonly items: readonly PendingInput[];
}

export type PendingInputAction =
  | { readonly type: "enqueue"; readonly item: PendingInput }
  | { readonly type: "accept_head"; readonly id: string }
  | { readonly type: "requeue_head"; readonly item: PendingInput }
  | { readonly type: "recall_tail" }
  | { readonly type: "discard_all" };

export type PendingInputEnqueueResult =
  | { readonly accepted: true; readonly item: PendingInput }
  | { readonly accepted: false; readonly reason: "full" | "terminal_barrier" };

export function createPendingInput(raw: string): PendingInput {
  return {
    id: `pending_${randomUUID().replaceAll("-", "")}`,
    raw,
    terminalBarrier: isTerminalBarrier(raw),
  };
}

export function isTerminalBarrier(raw: string): boolean {
  return /^\s*\/quit(?:\s|$)/u.test(raw);
}
