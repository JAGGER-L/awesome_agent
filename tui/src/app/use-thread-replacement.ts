import { useCallback } from "react";

import type { CommandController } from "../commands/controller.js";
import type { SurfaceStore } from "../state/index.js";
import { hydrateThreadPage } from "../transcript/hydrate.js";

export interface ThreadReplacementRequest {
  readonly threadId: string;
  readonly expectedGeneration: number;
  readonly reason: "new" | "resume";
}

export type ThreadReplacementResult =
  | { readonly kind: "replaced"; readonly generation: number }
  | { readonly kind: "stale" };

export class ThreadReplacementError extends Error {
  readonly code = "thread_replacement_invalid";
}

interface ThreadReplacementDependencies {
  readonly store: SurfaceStore;
  readonly controller?: CommandController | undefined;
  readonly resetThreadScope?: (() => void) | undefined;
}

export async function replaceThreadSurface({
  store,
  controller,
  request,
  resetThreadScope,
}: ThreadReplacementDependencies & {
  readonly request: ThreadReplacementRequest;
}): Promise<ThreadReplacementResult> {
  if (!controller) {
    throw new ThreadReplacementError("Thread controller is unavailable.");
  }
  const replacement = await controller.loadThreadReplacement(request.threadId);
  if (store.getState().thread_generation !== request.expectedGeneration) {
    return { kind: "stale" };
  }
  if (replacement.kind === "error") {
    throw new ThreadReplacementError(replacement.error.message);
  }
  if (
    replacement.application.current_thread_id !== request.threadId ||
    replacement.thread.view.thread.id !== request.threadId
  ) {
    throw new ThreadReplacementError(
      "Thread replacement identities do not match the selected Thread.",
    );
  }

  const transcript =
    request.reason === "new"
      ? [
          {
            key: `thread-start:${request.threadId}`,
            kind: "status" as const,
            message: "New conversation started",
          },
        ]
      : hydrateThreadPage(replacement.thread).blocks;
  store.dispatch({
    type: "thread.replaced",
    application: replacement.application,
    thread: replacement.thread,
    transcript,
    transcript_persisted: request.reason === "resume",
  });
  resetThreadScope?.();
  return {
    kind: "replaced",
    generation: store.getState().thread_generation,
  };
}

export function useThreadReplacement({
  store,
  controller,
  resetThreadScope,
}: ThreadReplacementDependencies) {
  return useCallback(
    async (request: ThreadReplacementRequest) =>
      await replaceThreadSurface({
        store,
        controller,
        request,
        resetThreadScope,
      }),
    [controller, resetThreadScope, store],
  );
}
