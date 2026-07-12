import type { SurfaceAction } from "./actions.js";
import type { SurfaceState } from "./model.js";
import { initialSurfaceState, surfaceReducer } from "./reducer.js";

export interface SurfaceStore {
  getState(): SurfaceState;
  dispatch(action: SurfaceAction): void;
  subscribe(listener: () => void): () => void;
}

export class StoreInvariantError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StoreInvariantError";
  }
}

export function createSurfaceStore(
  seed: SurfaceState = initialSurfaceState(),
): SurfaceStore {
  let state = seed;
  let dispatching = false;
  const listeners = new Set<() => void>();
  return {
    getState: () => state,
    dispatch(action) {
      if (dispatching)
        throw new StoreInvariantError("Nested Surface dispatch is forbidden");
      dispatching = true;
      try {
        const next = surfaceReducer(state, action);
        if (next === state) return;
        state = next;
        for (const listener of [...listeners]) listener();
      } finally {
        dispatching = false;
      }
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
