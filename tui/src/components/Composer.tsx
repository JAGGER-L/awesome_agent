import { Box, Text } from "ink";

import { graphemes } from "../composer/graphemes.js";
import type { ComposerState } from "../composer/model.js";
import { displayWidth } from "../composer/viewport.js";
import { useTheme } from "./theme.js";

export function Composer({
  state,
  message,
  submitting = false,
}: {
  readonly state: ComposerState;
  readonly message?: string;
  readonly submitting?: boolean;
}) {
  const theme = useTheme();
  const [beforeCursor, afterCursor] = splitVisibleAtCursor(
    state.viewport.rows,
    state.viewport.cursorRow,
    state.viewport.cursorColumn,
  );

  return (
    <Box flexDirection="column">
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
          <Text color={theme.primary}>▌</Text>
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
