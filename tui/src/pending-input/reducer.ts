import {
  MAX_PENDING_INPUTS,
  type PendingInputAction,
  type PendingInputState,
} from "./model.js";

export function initialPendingInputState(): PendingInputState {
  return { items: [] };
}

export function pendingInputReducer(
  state: PendingInputState,
  action: PendingInputAction,
): PendingInputState {
  switch (action.type) {
    case "enqueue":
      if (
        state.items.length >= MAX_PENDING_INPUTS ||
        state.items.some((item) => item.terminalBarrier) ||
        state.items.some((item) => item.id === action.item.id)
      ) {
        return state;
      }
      return { items: [...state.items, action.item] };
    case "accept_head":
      return state.items[0]?.id === action.id
        ? { items: state.items.slice(1) }
        : state;
    case "requeue_head": {
      const withoutDuplicate = state.items.filter(
        (item) => item.id !== action.item.id,
      );
      if (withoutDuplicate.length >= MAX_PENDING_INPUTS) return state;
      return { items: [action.item, ...withoutDuplicate] };
    }
    case "recall_tail":
      return state.items.length === 0
        ? state
        : { items: state.items.slice(0, -1) };
    case "discard_all":
      return state.items.length === 0 ? state : initialPendingInputState();
  }
}
