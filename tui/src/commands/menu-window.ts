import type { CommandMetadata } from "./catalog.js";
import type { CommandName } from "../protocol/commands.js";

export interface CommandMenuWindow {
  readonly items: readonly CommandMetadata[];
  readonly start: number;
  readonly end: number;
  readonly total: number;
}

export function commandMenuWindow(
  matches: readonly CommandMetadata[],
  selectedCommand: CommandName | undefined,
  viewportStart: number,
  viewportSize = 10,
): CommandMenuWindow {
  const selected = Math.max(
    0,
    matches.findIndex((item) => item.name === selectedCommand),
  );
  const maximumStart = Math.max(0, matches.length - viewportSize);
  let visibleStart = Math.max(0, Math.min(viewportStart, maximumStart));
  if (selected < visibleStart) visibleStart = selected;
  else if (selected >= visibleStart + viewportSize) {
    visibleStart = selected - viewportSize + 1;
  }
  visibleStart = Math.max(0, Math.min(visibleStart, maximumStart));
  const end = Math.min(matches.length, visibleStart + viewportSize);
  return {
    items: matches.slice(visibleStart, end),
    start: visibleStart,
    end,
    total: matches.length,
  };
}
