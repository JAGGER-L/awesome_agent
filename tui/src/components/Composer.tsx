import { Box, Text, useBoxMetrics, type DOMElement } from "ink";
import { useRef } from "react";

import { graphemes } from "../composer/graphemes.js";
import type { ComposerState } from "../composer/model.js";
import { displayWidth } from "../composer/viewport.js";
import { useTheme } from "./theme.js";
import { useComposerCursor } from "./use-composer-cursor.js";

export function Composer({
  state,
  message,
  submitting = false,
  active = true,
}: {
  readonly state: ComposerState;
  readonly message?: string;
  readonly submitting?: boolean;
  readonly active?: boolean;
}) {
  const theme = useTheme();
  const composerRef = useRef<DOMElement>(null);
  const metrics = useBoxMetrics(composerRef);
  useComposerCursor({
    active: active && !submitting,
    elementRef: composerRef,
    metrics,
    cursorRow: state.viewport.cursorRow,
    cursorColumn: state.viewport.cursorColumn,
    hiddenAbove: state.viewport.hiddenAbove,
  });
  const [beforeCursor, afterCursor] = splitVisibleAtCursor(
    state.viewport.rows,
    state.viewport.cursorRow,
    state.viewport.cursorColumn,
  );

  return (
    <Box ref={composerRef} flexDirection="column">
      <Box
        borderStyle="round"
        borderColor={theme.border}
        paddingX={1}
        flexDirection="column"
      >
        <Text color={theme.secondary}>
          {submitting ? "Sending…" : "Message"}
        </Text>
        {state.viewport.hiddenAbove ? <Text dimColor>↑ more</Text> : null}
        <Text>
          <Text bold color={theme.primary}>
            ❯{" "}
          </Text>
          {beforeCursor}
          {afterCursor}
        </Text>
        {state.viewport.hiddenBelow ? <Text dimColor>↓ more</Text> : null}
      </Box>
      {state.error ? <Text color={theme.danger}>{state.error}</Text> : null}
      {message ? <Text color={theme.warning}>{message}</Text> : null}
    </Box>
  );
}

function splitVisibleAtCursor(
  rows: readonly string[],
  cursorRow: number,
  cursorColumn: number,
): readonly [string, string] {
  const row = rows[cursorRow] ?? "";
  let width = 0;
  let offset = 0;
  for (const part of graphemes(row)) {
    if (width >= cursorColumn) break;
    width += displayWidth(part);
    offset += part.length;
  }
  const beforeRows = rows.slice(0, cursorRow);
  const afterRows = rows.slice(cursorRow + 1);
  return [
    [...beforeRows, row.slice(0, offset)].join("\n"),
    [row.slice(offset), ...afterRows].join("\n"),
  ];
}
