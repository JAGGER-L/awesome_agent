import type { TerminalUiState } from "../interaction/model.js";

export function isAuthPicker(mode: TerminalUiState["mode"]): mode is Extract<
  TerminalUiState["mode"],
  { kind: "picker" }
> & {
  owner: { kind: "command"; intent: { name: "auth" } };
} {
  return (
    mode.kind === "picker" &&
    mode.owner.kind === "command" &&
    mode.owner.intent.name === "auth"
  );
}

export function unavailableSelectionMessage(
  disabled: boolean | undefined,
): string | undefined {
  return disabled ? "This credential source is not available." : undefined;
}
