import type { BoxMetrics, CursorPosition, DOMElement } from "ink";
import { useEffect, useInsertionEffect, type RefObject } from "react";

import { graphemes } from "../composer/graphemes.js";
import { displayWidth } from "../composer/viewport.js";
import { adaptInkCursorPosition } from "./cursor/ink-cursor-bridge.js";
import { useTerminalFrameMetrics } from "./cursor/terminal-frame-metrics.js";

const COMPOSER_CONTENT_OFFSET = 2;
const COMPOSER_PROMPT = "❯ ";
const COMPOSER_PROMPT_WIDTH = graphemes(COMPOSER_PROMPT).reduce(
  (width, grapheme) => width + displayWidth(grapheme),
  0,
);

export interface ComposerCursorPositionOptions {
  readonly active: boolean;
  readonly metrics: BoxMetrics & { readonly hasMeasured: boolean };
  readonly cursorRow: number;
  readonly cursorColumn: number;
  readonly hiddenAbove: boolean;
}

export interface ComposerCursorOptions extends ComposerCursorPositionOptions {
  readonly elementRef: RefObject<DOMElement | null>;
}

export function resolveComposerCursorPosition({
  active,
  metrics,
  cursorRow,
  cursorColumn,
  hiddenAbove,
}: ComposerCursorPositionOptions): CursorPosition | undefined {
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
  const frame = useTerminalFrameMetrics();

  // The first measured Composer render can be child-only. Explicitly ask the
  // ancestor cursor owner to commit the position instead of depending on an
  // unrelated frame-size update.
  useEffect(() => {
    if (options.metrics.hasMeasured) frame.requestCursorCommit();
  }, [frame.requestCursorCommit, options.metrics.hasMeasured]);

  // This intentionally runs on every commit. The Composer can move when an
  // upstream sibling grows even if all local cursor inputs stay unchanged.
  useInsertionEffect(() => {
    const clearCursor = () => frame.publishCursor(undefined);
    if (!options.active || !options.metrics.hasMeasured || !frame.hasMeasured) {
      clearCursor();
      return clearCursor;
    }

    const root = findInkRoot(options.elementRef.current);
    if (
      !root?.onComputeLayout ||
      root !== findInkRoot(frame.frameRef.current)
    ) {
      clearCursor();
      return clearCursor;
    }

    // Ink publishes useCursor during insertion effects, before its normal
    // resetAfterCommit layout pass. Recompute Yoga now so the current content
    // and physical cursor are emitted from the same layout.
    root.onComputeLayout();
    const composer = readCurrentMetrics(options.elementRef.current);
    const currentFrame = readCurrentMetrics(frame.frameRef.current);
    if (!composer || !currentFrame) {
      clearCursor();
      return clearCursor;
    }

    const logical = resolveComposerCursorPosition({
      active: true,
      metrics: composer,
      cursorRow: options.cursorRow,
      cursorColumn: options.cursorColumn,
      hiddenAbove: options.hiddenAbove,
    });
    frame.publishCursor(
      adaptInkCursorPosition(logical, {
        frameHeight: currentFrame.height,
        terminalRows: frame.terminalRows,
        hasMeasured: true,
      }),
    );
    return clearCursor;
  });
}

function findInkRoot(node: DOMElement | null): DOMElement | undefined {
  let current: DOMElement | undefined = node ?? undefined;
  while (current?.parentNode) current = current.parentNode;
  return current?.nodeName === "ink-root" ? current : undefined;
}

function readCurrentMetrics(
  node: DOMElement | null,
): (BoxMetrics & { readonly hasMeasured: true }) | undefined {
  const layout = node?.yogaNode?.getComputedLayout();
  if (!layout) return undefined;
  return {
    width: layout.width,
    height: layout.height,
    left: layout.left,
    top: layout.top,
    hasMeasured: true,
  };
}
