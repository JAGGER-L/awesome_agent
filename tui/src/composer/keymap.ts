import type { ComposerAction } from "./model.js";

export interface ComposerKey {
  readonly return: boolean;
  readonly ctrl: boolean;
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

export type ComposerKeyAction =
  | { readonly type: "edit"; readonly action: ComposerAction }
  | { readonly type: "submit" };

export function mapComposerKey(
  input: string,
  key: ComposerKey,
  composerEmpty: boolean,
): ComposerKeyAction | undefined {
  if (key.return && ((key.ctrl && input.toLowerCase() === "j") || key.shift)) {
    return { type: "edit", action: { type: "insert", text: "\n" } };
  }
  if (key.return) return { type: "submit" };
  if (key.backspace) return edit({ type: "backspace" });
  if (key.delete) return edit({ type: "delete" });
  if (key.leftArrow) return edit({ type: "left" });
  if (key.rightArrow) return edit({ type: "right" });
  if (key.home) return edit({ type: "home" });
  if (key.end) return edit({ type: "end" });
  if (key.upArrow && composerEmpty) return edit({ type: "history_previous" });
  if (key.downArrow && composerEmpty) return edit({ type: "history_next" });
  if (key.ctrl) {
    switch (input.toLowerCase()) {
      case "a":
        return edit({ type: "buffer_home" });
      case "e":
        return edit({ type: "buffer_end" });
      case "u":
        return edit({ type: "delete_line_start" });
      case "k":
        return edit({ type: "delete_line_end" });
      case "w":
        return edit({ type: "delete_word" });
      case "j":
        return edit({ type: "insert", text: "\n" });
      default:
        return undefined;
    }
  }
  if (input.length > 0) return edit({ type: "insert", text: input });
  return undefined;
}

function edit(action: ComposerAction): ComposerKeyAction {
  return { type: "edit", action };
}
