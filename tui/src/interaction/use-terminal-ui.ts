import { useCallback, useReducer, useRef } from "react";

import type { TerminalUiAction, TerminalUiState } from "./model.js";
import { terminalUiReducer } from "./reducer.js";

export function useTerminalUi(initial: TerminalUiState) {
  const [state, reactDispatch] = useReducer(terminalUiReducer, initial);
  const current = useRef(state);
  current.current = state;

  const dispatch = useCallback((action: TerminalUiAction) => {
    current.current = terminalUiReducer(current.current, action);
    reactDispatch(action);
  }, []);

  return { state, current, dispatch } as const;
}
