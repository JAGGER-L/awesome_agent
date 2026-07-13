import { useCallback } from "react";

import type { ThreadTransitionSnapshot } from "../protocol/commands.js";
import type { SurfaceStore } from "../state/index.js";
import { hydrateThreadPage } from "../transcript/hydrate.js";

export interface ThreadReplacementRequest {
  readonly transition: ThreadTransitionSnapshot;
  readonly expectedGeneration: number;
}

export type ThreadReplacementResult =
  | { readonly kind: "replaced"; readonly generation: number }
  | { readonly kind: "stale" };

export class ThreadReplacementError extends Error {
  readonly code = "thread_replacement_invalid";
}

interface ThreadReplacementDependencies {
  readonly store: SurfaceStore;
  readonly resetThreadScope?: (() => void) | undefined;
}

export async function replaceThreadSurface({
  store,
  request,
  resetThreadScope,
}: ThreadReplacementDependencies & {
  readonly request: ThreadReplacementRequest;
}): Promise<ThreadReplacementResult> {
  if (store.getState().thread_generation !== request.expectedGeneration) {
    return { kind: "stale" };
  }
  const { transition } = request;
  const threadId = transition.thread.view.thread.id;
  if (transition.application.current_thread_id !== threadId) {
    throw new ThreadReplacementError(
      "Thread replacement identities do not match the selected Thread.",
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
    transcript_persisted: transition.reason === "resume",
  });
  resetThreadScope?.();
  return {
    kind: "replaced",
    generation: store.getState().thread_generation,
  };
}

export function useThreadReplacement({
  store,
  resetThreadScope,
}: ThreadReplacementDependencies) {
  return useCallback(
    async (request: ThreadReplacementRequest) =>
      await replaceThreadSurface({
        store,
        request,
        resetThreadScope,
      }),
    [resetThreadScope, store],
  );
}
