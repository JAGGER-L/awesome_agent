import { useCallback } from "react";

import type { ThreadTransitionSnapshot } from "../protocol/commands.js";
import type { SurfaceStore } from "../state/index.js";
import { hydrateThreadPage } from "../transcript/hydrate.js";

export interface ThreadTransitionEffects {
  readonly resetThreadScope?: () => void;
  readonly resetCurrentFrame: () => void;
}

export type ThreadTransitionResult =
  | { readonly kind: "replaced"; readonly generation: number }
  | { readonly kind: "stale" };

export class ThreadTransitionError extends Error {
  readonly code = "thread_transition_invalid";
}

export function applyThreadTransition(input: {
  readonly store: SurfaceStore;
  readonly transition: ThreadTransitionSnapshot;
  readonly expectedGeneration: number;
  readonly effects: ThreadTransitionEffects;
}): ThreadTransitionResult {
  const { store, transition, expectedGeneration, effects } = input;
  if (store.getState().thread_generation !== expectedGeneration) {
    return { kind: "stale" };
  }
  const threadId = transition.thread.view.thread.id;
  if (transition.application.current_thread_id !== threadId) {
    throw new ThreadTransitionError(
      "Thread transition identities do not match the selected Thread.",
    );
  }

  const transcript =
    transition.reason === "new"
      ? [
          {
            key: `thread-start:${threadId}`,
            kind: "status" as const,
            message: "New conversation started",
          },
        ]
      : hydrateThreadPage(transition.thread).blocks;
  store.dispatch({
    type: "thread.replaced",
    application: transition.application,
    thread: transition.thread,
    transcript,
  });
  effects.resetThreadScope?.();
  effects.resetCurrentFrame();
  return {
    kind: "replaced",
    generation: store.getState().thread_generation,
  };
}

export function useThreadTransition(input: {
  readonly store: SurfaceStore;
  readonly effects: ThreadTransitionEffects;
}) {
  return useCallback(
    (transition: ThreadTransitionSnapshot, expectedGeneration: number) =>
      applyThreadTransition({
        store: input.store,
        transition,
        expectedGeneration,
        effects: input.effects,
      }),
    [input.effects, input.store],
  );
}
