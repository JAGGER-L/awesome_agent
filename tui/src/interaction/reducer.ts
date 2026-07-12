import { composerReducer, initialComposerState } from "../composer/reducer.js";
import { graphemes } from "../composer/graphemes.js";
import type { TerminalUiAction, TerminalUiState } from "./model.js";

export function initialTerminalUiState(): TerminalUiState {
  return {
    mode: { kind: "composer" },
    composer: initialComposerState(),
    composerSubmitting: false,
  };
}

export function terminalUiReducer(
  state: TerminalUiState,
  action: TerminalUiAction,
): TerminalUiState {
  switch (action.type) {
    case "mode.open":
      return { ...state, mode: action.mode };
    case "mode.cancel":
      return { ...state, mode: { kind: "composer" } };
    case "mode.select":
      return { ...state, mode: moveSelection(state.mode, action.delta) };
    case "mode.set":
      return { ...state, mode: setSelection(state.mode, action.selected) };
    case "mode.secret.insert":
      return updateSecret(state, (mode) => ({
        ...mode,
        value: mode.value + action.text,
        message: undefined,
      }));
    case "mode.secret.backspace":
      return updateSecret(state, (mode) => ({
        ...mode,
        value: graphemes(mode.value).slice(0, -1).join(""),
        message: undefined,
      }));
    case "mode.secret.submitting":
      return updateSecret(state, (mode) => ({
        ...mode,
        submitting: action.submitting,
      }));
    case "mode.secret.message":
      return updateSecret(state, (mode) => ({
        ...mode,
        message: action.message,
      }));
    case "mode.approval.submitting":
      return updateApproval(state, (mode) => ({
        ...mode,
        submitting: action.submitting,
      }));
    case "mode.approval.message":
      return updateApproval(state, (mode) => ({
        ...mode,
        message: action.message,
      }));
    case "mode.trust.submitting":
      return updateTrust(state, (mode) => ({
        ...mode,
        submitting: action.submitting,
      }));
    case "mode.trust.message":
      return updateTrust(state, (mode) => ({
        ...mode,
        message: action.message,
      }));
    case "composer.edit":
      return {
        ...state,
        composer: composerReducer(state.composer, action.action),
      };
    case "composer.submitting":
      return { ...state, composerSubmitting: action.submitting };
    case "composer.message":
      return action.message === undefined
        ? withoutComposerMessage(state)
        : { ...state, composerMessage: action.message };
    case "notice.set":
      return { ...state, notice: action.message };
    case "notice.clear":
      return withoutNotice(state);
  }
}

function moveSelection(
  mode: TerminalUiState["mode"],
  delta: -1 | 1,
): TerminalUiState["mode"] {
  const size =
    mode.kind === "picker"
      ? mode.selection.options.length
      : mode.kind === "approval"
        ? mode.interaction.choices.length
        : mode.kind === "workspace_trust" || mode.kind === "fatal"
          ? 2
          : mode.kind === "permission_confirmation"
            ? 2
            : 0;
  if (size === 0 || !("selected" in mode)) return mode;
  return { ...mode, selected: (mode.selected + delta + size) % size };
}

function setSelection(
  mode: TerminalUiState["mode"],
  selected: number,
): TerminalUiState["mode"] {
  const size = selectionSize(mode);
  if (size === 0 || !("selected" in mode)) return mode;
  return { ...mode, selected: Math.max(0, Math.min(size - 1, selected)) };
}

function selectionSize(mode: TerminalUiState["mode"]): number {
  if (mode.kind === "picker") return mode.selection.options.length;
  if (mode.kind === "approval") return mode.interaction.choices.length;
  if (
    mode.kind === "workspace_trust" ||
    mode.kind === "fatal" ||
    mode.kind === "permission_confirmation"
  ) {
    return 2;
  }
  return 0;
}

function updateSecret(
  state: TerminalUiState,
  update: (
    mode: Extract<TerminalUiState["mode"], { kind: "secret" }>,
  ) => Extract<TerminalUiState["mode"], { kind: "secret" }>,
): TerminalUiState {
  return state.mode.kind === "secret"
    ? { ...state, mode: update(state.mode) }
    : state;
}

function updateApproval(
  state: TerminalUiState,
  update: (
    mode: Extract<TerminalUiState["mode"], { kind: "approval" }>,
  ) => Extract<TerminalUiState["mode"], { kind: "approval" }>,
): TerminalUiState {
  return state.mode.kind === "approval"
    ? { ...state, mode: update(state.mode) }
    : state;
}

function updateTrust(
  state: TerminalUiState,
  update: (
    mode: Extract<TerminalUiState["mode"], { kind: "workspace_trust" }>,
  ) => Extract<TerminalUiState["mode"], { kind: "workspace_trust" }>,
): TerminalUiState {
  return state.mode.kind === "workspace_trust"
    ? { ...state, mode: update(state.mode) }
    : state;
}

function withoutComposerMessage(state: TerminalUiState): TerminalUiState {
  const { composerMessage: _message, ...next } = state;
  void _message;
  return next;
}

function withoutNotice(state: TerminalUiState): TerminalUiState {
  const { notice: _notice, ...next } = state;
  void _notice;
  return next;
}
