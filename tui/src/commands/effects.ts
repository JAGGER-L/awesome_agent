import type { SurfaceStore } from "../state/index.js";
import type { CommandPayload } from "../protocol/commands.js";

export function applyCommandEffect(
  payload: CommandPayload,
  store: Pick<SurfaceStore, "dispatch">,
): void {
  if (payload.kind === "thread_renamed") {
    store.dispatch({ type: "thread.metadata.updated", thread: payload.thread });
  }
}
