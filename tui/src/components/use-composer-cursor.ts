import { useCursor, type BoxMetrics, type CursorPosition } from "ink";

import { graphemes } from "../composer/graphemes.js";
import { displayWidth } from "../composer/viewport.js";

const COMPOSER_CONTENT_OFFSET = 2;
const COMPOSER_PROMPT = "❯ ";
const COMPOSER_PROMPT_WIDTH = graphemes(COMPOSER_PROMPT).reduce(
  (width, grapheme) => width + displayWidth(grapheme),
  0,
);

export interface ComposerCursorOptions {
  readonly active: boolean;
  readonly metrics: BoxMetrics & { readonly hasMeasured: boolean };
  readonly cursorRow: number;
  readonly cursorColumn: number;
  readonly hiddenAbove: boolean;
}

export function resolveComposerCursorPosition({
  active,
  metrics,
  cursorRow,
  cursorColumn,
  hiddenAbove,
}: ComposerCursorOptions): CursorPosition | undefined {
  if (!active || !metrics.hasMeasured) return undefined;
  return {
    x:
      metrics.left +
      COMPOSER_CONTENT_OFFSET +
      COMPOSER_PROMPT_WIDTH +
      cursorColumn,
    y: metrics.top + COMPOSER_CONTENT_OFFSET + Number(hiddenAbove) + cursorRow,
  };
}

export function useComposerCursor(options: ComposerCursorOptions): void {
  const { setCursorPosition } = useCursor();
  setCursorPosition(resolveComposerCursorPosition(options));
}
