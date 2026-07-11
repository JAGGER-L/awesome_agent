import { Box, Text, useInput } from "ink";
import { useEffect, useReducer, useRef, useState } from "react";

import { mapComposerKey } from "../composer/keymap.js";
import { graphemes } from "../composer/graphemes.js";
import { composerReducer, initialComposerState } from "../composer/reducer.js";
import { displayWidth } from "../composer/viewport.js";
import { useTheme } from "./theme.js";

export interface ComposerSubmitResult {
  readonly accepted: boolean;
  readonly retryable?: boolean;
  readonly message?: string;
}

export function Composer({
  width,
  initialValue = "",
  active = true,
  clearRevision = 0,
  onSubmit,
  onValueChange,
}: {
  readonly width: number;
  readonly initialValue?: string;
  readonly active?: boolean;
  readonly clearRevision?: number;
  readonly onSubmit: (
    value: string,
  ) => Promise<ComposerSubmitResult> | ComposerSubmitResult;
  readonly onValueChange?: (value: string) => void;
}) {
  const [state, dispatch] = useReducer(composerReducer, undefined, () => {
    let initial = initialComposerState();
    initial = composerReducer(initial, { type: "resize", width });
    return initialValue
      ? composerReducer(initial, { type: "replace", value: initialValue })
      : initial;
  });
  const [message, setMessage] = useState<string>();
  const [submitting, setSubmitting] = useState(false);
  const stateRef = useRef(state);
  const clearRevisionRef = useRef(clearRevision);
  const theme = useTheme();
  const [beforeCursor, afterCursor] = splitVisibleAtCursor(
    state.viewport.rows,
    state.viewport.cursorRow,
    state.viewport.cursorColumn,
  );

  stateRef.current = state;
  const apply = (action: Parameters<typeof composerReducer>[1]) => {
    stateRef.current = composerReducer(stateRef.current, action);
    dispatch(action);
  };

  useEffect(() => {
    const action = { type: "resize" as const, width };
    stateRef.current = composerReducer(stateRef.current, action);
    dispatch(action);
  }, [width]);
  useEffect(() => {
    if (clearRevision === clearRevisionRef.current) return;
    clearRevisionRef.current = clearRevision;
    const action = { type: "replace" as const, value: "" };
    stateRef.current = composerReducer(stateRef.current, action);
    dispatch(action);
  }, [clearRevision]);
  useEffect(() => onValueChange?.(state.value), [onValueChange, state.value]);

  useInput(
    (input, key) => {
      if (submitting) return;
      const current = stateRef.current;
      const mapped = mapComposerKey(input, key, current.value.length === 0);
      if (!mapped) return;
      if (mapped.type === "edit") {
        setMessage(undefined);
        apply(mapped.action);
        return;
      }
      if (current.value.trim().length === 0) return;
      const submitted = current.value;
      setSubmitting(true);
      void Promise.resolve(onSubmit(submitted))
        .then((result) => {
          if (result.accepted) {
            apply({ type: "submit_history", value: submitted });
            apply({ type: "replace", value: "" });
          }
          setMessage(result.message);
        })
        .finally(() => setSubmitting(false));
    },
    { isActive: active },
  );

  return (
    <Box flexDirection="column">
      <Text color={theme.muted}>Message</Text>
      {state.viewport.hiddenAbove ? <Text dimColor>↑ more</Text> : null}
      <Text>
        {beforeCursor}
        <Text color={theme.accent}>▌</Text>
        {afterCursor}
      </Text>
      {state.viewport.hiddenBelow ? <Text dimColor>↓ more</Text> : null}
      {state.error ? <Text color={theme.error}>{state.error}</Text> : null}
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
  const before = [...beforeRows, row.slice(0, offset)].join("\n");
  const after = [row.slice(offset), ...afterRows].join("\n");
  return [before, after];
}
