import type { CursorPosition } from "ink";

import type { TerminalFrameMetrics } from "./terminal-frame-metrics.js";

export function adaptInkCursorPosition(
  position: CursorPosition | undefined,
  frame: TerminalFrameMetrics,
): CursorPosition | undefined {
  if (!position || !frame.hasMeasured || frame.terminalRows <= 0) {
    return undefined;
  }
  // Ink 7.1 omits the trailing newline for fullscreen frames, while its cursor
  // helper still measures from the following physical row.
  return frame.frameHeight >= frame.terminalRows
    ? { x: position.x, y: position.y + 1 }
    : position;
}
