import {
  codePointCount,
  codeUnitOffset,
  graphemeCount,
  graphemes,
} from "./graphemes.js";
import { appendHistory } from "./history.js";
import type { ComposerAction, ComposerState } from "./model.js";
import { computeViewport } from "./viewport.js";

export const MAX_COMPOSER_CODE_POINTS = 200_000;

export function initialComposerState(): ComposerState {
  return {
    value: "",
    cursorGrapheme: 0,
    viewport: {
      width: 80,
      startRow: 0,
      rows: [""],
      cursorRow: 0,
      cursorColumn: 0,
      hiddenAbove: false,
      hiddenBelow: false,
    },
    history: [],
    historyIndex: null,
    draft: "",
  };
}

export function composerReducer(
  state: ComposerState,
  action: ComposerAction,
): ComposerState {
  if (action.type === "resize") {
    return withViewport(state, action.width);
  }
  if (action.type === "submit_history") {
    return {
      ...state,
      history: appendHistory(state.history, action.value),
      historyIndex: null,
      draft: "",
    };
  }
  if (action.type === "history_previous") {
    if (state.history.length === 0) return state;
    const index =
      state.historyIndex === null
        ? state.history.length - 1
        : Math.max(0, state.historyIndex - 1);
    return replaceFromHistory(
      state,
      index,
      state.historyIndex === null ? state.value : state.draft,
    );
  }
  if (action.type === "history_next") {
    if (state.historyIndex === null) return state;
    if (state.historyIndex === state.history.length - 1) {
      return withViewport({
        ...state,
        value: state.draft,
        cursorGrapheme: graphemeCount(state.draft),
        historyIndex: null,
      });
    }
    return replaceFromHistory(state, state.historyIndex + 1, state.draft);
  }

  switch (action.type) {
    case "insert": {
      const offset = codeUnitOffset(state.value, state.cursorGrapheme);
      const value =
        state.value.slice(0, offset) + action.text + state.value.slice(offset);
      if (codePointCount(value) > MAX_COMPOSER_CODE_POINTS) {
        return { ...state, error: "input_too_large" };
      }
      return withViewport(
        clearError({
          ...state,
          value,
          cursorGrapheme: state.cursorGrapheme + graphemeCount(action.text),
        }),
      );
    }
    case "replace":
      return codePointCount(action.value) > MAX_COMPOSER_CODE_POINTS
        ? { ...state, error: "input_too_large" }
        : withViewport(
            clearError({
              ...state,
              value: action.value,
              cursorGrapheme: graphemeCount(action.value),
            }),
          );
    case "left":
      return withViewport(
        clearError({
          ...state,
          cursorGrapheme: Math.max(0, state.cursorGrapheme - 1),
        }),
      );
    case "right":
      return withViewport(
        clearError({
          ...state,
          cursorGrapheme: Math.min(
            graphemeCount(state.value),
            state.cursorGrapheme + 1,
          ),
        }),
      );
    case "backspace":
      return removeRange(
        state,
        Math.max(0, state.cursorGrapheme - 1),
        state.cursorGrapheme,
      );
    case "delete":
      return removeRange(state, state.cursorGrapheme, state.cursorGrapheme + 1);
    case "buffer_home":
      return withViewport(clearError({ ...state, cursorGrapheme: 0 }));
    case "buffer_end":
      return withViewport(
        clearError({
          ...state,
          cursorGrapheme: graphemeCount(state.value),
        }),
      );
    case "home":
      return withViewport(
        clearError({
          ...state,
          cursorGrapheme: lineBoundary(state, "start"),
        }),
      );
    case "end":
      return withViewport(
        clearError({
          ...state,
          cursorGrapheme: lineBoundary(state, "end"),
        }),
      );
    case "delete_line_start":
      return removeRange(
        state,
        lineBoundary(state, "start"),
        state.cursorGrapheme,
      );
    case "delete_line_end":
      return removeRange(
        state,
        state.cursorGrapheme,
        lineBoundary(state, "end"),
      );
    case "delete_word": {
      const parts = graphemes(state.value);
      let start = state.cursorGrapheme;
      while (start > 0 && /^\s$/u.test(parts[start - 1] ?? "")) start -= 1;
      while (start > 0 && !/^\s$/u.test(parts[start - 1] ?? "")) start -= 1;
      return removeRange(state, start, state.cursorGrapheme);
    }
  }
}

function removeRange(
  state: ComposerState,
  start: number,
  end: number,
): ComposerState {
  const parts = graphemes(state.value);
  const boundedStart = Math.max(0, Math.min(parts.length, start));
  const boundedEnd = Math.max(boundedStart, Math.min(parts.length, end));
  return withViewport(
    clearError({
      ...state,
      value: [...parts.slice(0, boundedStart), ...parts.slice(boundedEnd)].join(
        "",
      ),
      cursorGrapheme: boundedStart,
    }),
  );
}

function lineBoundary(state: ComposerState, side: "start" | "end"): number {
  const parts = graphemes(state.value);
  if (side === "start") {
    const newline = parts.slice(0, state.cursorGrapheme).lastIndexOf("\n");
    return newline + 1;
  }
  const relative = parts.slice(state.cursorGrapheme).indexOf("\n");
  return relative < 0 ? parts.length : state.cursorGrapheme + relative;
}

function clearError(state: ComposerState): ComposerState {
  const { error: _error, ...next } = state;
  void _error;
  return next;
}

function withViewport(
  state: ComposerState,
  width = state.viewport.width,
): ComposerState {
  return {
    ...state,
    viewport: computeViewport(state.value, state.cursorGrapheme, width),
  };
}

function replaceFromHistory(
  state: ComposerState,
  historyIndex: number,
  draft: string,
): ComposerState {
  const value = state.history[historyIndex] ?? "";
  return withViewport({
    ...state,
    value,
    cursorGrapheme: graphemeCount(value),
    historyIndex,
    draft,
  });
}
