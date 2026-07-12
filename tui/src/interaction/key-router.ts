import { mapComposerKey } from "../composer/keymap.js";
import type { ComposerAction } from "../composer/model.js";
import type { TerminalUiState } from "./model.js";

export interface TerminalKey {
  readonly return: boolean;
  readonly escape: boolean;
  readonly tab: boolean;
  readonly ctrl: boolean;
  readonly meta: boolean;
  readonly shift: boolean;
  readonly backspace: boolean;
  readonly delete: boolean;
  readonly leftArrow: boolean;
  readonly rightArrow: boolean;
  readonly home: boolean;
  readonly end: boolean;
  readonly upArrow: boolean;
  readonly downArrow: boolean;
}

export type TerminalIntent =
  | { readonly type: "mode.cancel" }
  | { readonly type: "selection.move"; readonly delta: -1 | 1 }
  | { readonly type: "selection.set"; readonly selected: number }
  | { readonly type: "selection.confirm" }
  | { readonly type: "approval.deny" }
  | { readonly type: "trust.deny" }
  | { readonly type: "command.complete" }
  | { readonly type: "secret.insert"; readonly text: string }
  | { readonly type: "secret.backspace" }
  | { readonly type: "secret.submit" }
  | { readonly type: "composer.edit"; readonly action: ComposerAction }
  | { readonly type: "composer.submit" }
  | { readonly type: "lifecycle.evaluate"; readonly input: string };

export function emptyTerminalKey(): TerminalKey {
  return {
    return: false,
    escape: false,
    tab: false,
    ctrl: false,
    meta: false,
    shift: false,
    backspace: false,
    delete: false,
    leftArrow: false,
    rightArrow: false,
    home: false,
    end: false,
    upArrow: false,
    downArrow: false,
  };
}

export function normalizeTerminalKey(
  key: Readonly<Partial<TerminalKey>>,
): TerminalKey {
  return { ...emptyTerminalKey(), ...key };
}

export function routeTerminalKey(
  state: TerminalUiState,
  input: string,
  key: TerminalKey,
): TerminalIntent | undefined {
  const { mode } = state;
  let intent: TerminalIntent | undefined;
  switch (mode.kind) {
    case "workspace_trust":
      if (!mode.submitting) {
        if (!key.ctrl && !key.meta && (input === "1" || input === "2")) {
          intent = { type: "selection.set", selected: Number(input) - 1 };
        } else if (key.escape) {
          intent = { type: "trust.deny" };
        } else {
          intent = routeSelectionKey(key, true);
        }
      }
      break;
    case "fatal":
      intent = routeSelectionKey(key, true);
      break;
    case "secret":
      if (!mode.submitting) {
        if (key.escape) intent = { type: "mode.cancel" };
        else if (key.return && mode.value.length > 0)
          intent = { type: "secret.submit" };
        else if (key.backspace || key.delete)
          intent = { type: "secret.backspace" };
        else if (!key.ctrl && !key.meta && input)
          intent = { type: "secret.insert", text: input };
      }
      break;
    case "approval":
      if (!mode.submitting) {
        intent = routeSelectionKey(key, false);
        if (key.escape) intent = { type: "approval.deny" };
      }
      break;
    case "picker":
      intent = routeSelectionKey(key, mode.blocking);
      break;
    case "command_menu":
      if (key.upArrow) intent = { type: "selection.move", delta: -1 };
      else if (key.downArrow) intent = { type: "selection.move", delta: 1 };
      else if (key.tab) intent = { type: "command.complete" };
      else if (key.return) intent = { type: "selection.confirm" };
      else if (key.escape) intent = { type: "mode.cancel" };
      break;
    case "help":
      if (key.escape) intent = { type: "mode.cancel" };
      break;
    case "composer": {
      if (state.composerSubmitting) break;
      const mapped = mapComposerKey(
        input,
        key,
        state.composer.value.length === 0,
      );
      if (mapped?.type === "edit") {
        intent = { type: "composer.edit", action: mapped.action };
      } else if (mapped?.type === "submit") {
        intent = { type: "composer.submit" };
      }
      break;
    }
  }

  if (intent) return intent;
  const control = key.ctrl ? input.toLowerCase() : "";
  return control === "c" || control === "d"
    ? { type: "lifecycle.evaluate", input }
    : undefined;
}

function routeSelectionKey(
  key: TerminalKey,
  blocking: boolean,
): TerminalIntent | undefined {
  if (key.upArrow) return { type: "selection.move", delta: -1 };
  if (key.downArrow) return { type: "selection.move", delta: 1 };
  if (key.return) return { type: "selection.confirm" };
  if (key.escape && !blocking) return { type: "mode.cancel" };
  return undefined;
}
