import type { SurfaceState } from "../state/model.js";

export function threadViewportKey(state: SurfaceState): string {
  return `thread:${state.thread_generation}:${state.application?.current_thread_id ?? "none"}`;
}
