import type { ExitReason } from "../lifecycle/exit.js";

export type GlobalKeyAction =
  | { readonly kind: "cancel" }
  | { readonly kind: "clear_composer" }
  | { readonly kind: "exit_hint" }
  | { readonly kind: "exit"; readonly reason: ExitReason };

export class GlobalKeyController {
  #firstIdleCtrlC: number | undefined;

  constructor(private readonly now: () => number = Date.now) {}

  handle({
    input,
    key,
    activeOperation,
    composerEmpty,
  }: {
    readonly input: string;
    readonly key: { readonly ctrl: boolean };
    readonly activeOperation: boolean;
    readonly composerEmpty: boolean;
  }): GlobalKeyAction | undefined {
    const control = key.ctrl ? input.toLowerCase() : "";
    if (control === "c" && activeOperation) {
      this.#firstIdleCtrlC = undefined;
      return { kind: "cancel" };
    }
    if (control === "c" && !composerEmpty) {
      this.#firstIdleCtrlC = undefined;
      return { kind: "clear_composer" };
    }
    if (control === "c") {
      const now = this.now();
      if (
        this.#firstIdleCtrlC !== undefined &&
        now - this.#firstIdleCtrlC <= 2_000
      ) {
        this.#firstIdleCtrlC = undefined;
        return { kind: "exit", reason: "double_ctrl_c" };
      }
      this.#firstIdleCtrlC = now;
      return { kind: "exit_hint" };
    }
    if (control === "d" && composerEmpty) {
      this.#firstIdleCtrlC = undefined;
      return { kind: "exit", reason: "ctrl_d" };
    }
    return undefined;
  }
}
