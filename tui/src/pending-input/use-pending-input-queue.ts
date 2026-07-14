import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import {
  createPendingInput,
  MAX_PENDING_INPUTS,
  type PendingInput,
  type PendingInputEnqueueResult,
} from "./model.js";
import { initialPendingInputState, pendingInputReducer } from "./reducer.js";

export interface PendingInputQueue {
  readonly items: readonly PendingInput[];
  readonly current: { readonly current: readonly PendingInput[] };
  enqueue(
    raw: string,
    constraints?: {
      readonly reserved?: number;
      readonly terminalBarrierInFlight?: boolean;
    },
  ): PendingInputEnqueueResult;
  acceptHead(id: string): void;
  requeueHead(item: PendingInput): void;
  recallTail(): PendingInput | undefined;
  discardAll(): void;
}

export function usePendingInputQueue(): PendingInputQueue {
  const [state, reactDispatch] = useReducer(
    pendingInputReducer,
    undefined,
    initialPendingInputState,
  );
  const current = useRef<readonly PendingInput[]>(state.items);
  current.current = state.items;

  const dispatch = useCallback(
    (action: Parameters<typeof pendingInputReducer>[1]) => {
      const next = pendingInputReducer({ items: current.current }, action);
      current.current = next.items;
      reactDispatch(action);
    },
    [],
  );
  const enqueue = useCallback(
    (
      raw: string,
      constraints: {
        readonly reserved?: number;
        readonly terminalBarrierInFlight?: boolean;
      } = {},
    ): PendingInputEnqueueResult => {
      if (
        constraints.terminalBarrierInFlight === true ||
        current.current.some((item) => item.terminalBarrier)
      ) {
        return { accepted: false, reason: "terminal_barrier" };
      }
      if (
        current.current.length + (constraints.reserved ?? 0) >=
        MAX_PENDING_INPUTS
      ) {
        return { accepted: false, reason: "full" };
      }
      const item = createPendingInput(raw);
      dispatch({ type: "enqueue", item });
      return { accepted: true, item };
    },
    [dispatch],
  );
  const acceptHead = useCallback(
    (id: string) => dispatch({ type: "accept_head", id }),
    [dispatch],
  );
  const requeueHead = useCallback(
    (item: PendingInput) => dispatch({ type: "requeue_head", item }),
    [dispatch],
  );
  const recallTail = useCallback(() => {
    const item = current.current.at(-1);
    if (item) dispatch({ type: "recall_tail" });
    return item;
  }, [dispatch]);
  const discardAll = useCallback(
    () => dispatch({ type: "discard_all" }),
    [dispatch],
  );

  return {
    items: state.items,
    current,
    enqueue,
    acceptHead,
    requeueHead,
    recallTail,
    discardAll,
  };
}

export type PendingInputPromotionResult =
  | { readonly kind: "consumed" }
  | { readonly kind: "requeue"; readonly item: PendingInput };

export function usePendingInputDrain({
  queue,
  blocked,
  promote,
  onError,
}: {
  readonly queue: PendingInputQueue;
  readonly blocked: boolean;
  readonly promote: (
    item: PendingInput,
  ) => Promise<PendingInputPromotionResult>;
  readonly onError: (error: unknown) => void;
}): PendingInput | undefined {
  const [inFlight, setInFlight] = useState<PendingInput>();
  const running = useRef(false);

  useEffect(() => {
    const head = queue.items[0];
    if (blocked || inFlight || running.current || !head) return;
    running.current = true;
    queue.acceptHead(head.id);
    setInFlight(head);
    void promote(head)
      .then((result) => {
        if (result.kind === "requeue") queue.requeueHead(result.item);
      })
      .catch((error: unknown) => {
        queue.requeueHead(head);
        onError(error);
      })
      .finally(() => {
        running.current = false;
        setInFlight(undefined);
      });
  }, [blocked, inFlight, onError, promote, queue]);

  return inFlight;
}
