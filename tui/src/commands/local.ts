import type { ClipboardAdapter } from "../adapters/clipboard.js";
import type { PickerSelection } from "../components/Picker.js";
import type { MethodValue } from "../protocol/methods.js";
import type { ThemePreference } from "../preferences/theme.js";
import { findCommand } from "./catalog.js";
import type { LocalCommandIntent } from "./parser.js";

export interface LocalCommandDependencies {
  readonly clipboard: ClipboardAdapter;
  readonly getThread: () => MethodValue["thread.read"] | undefined;
  readonly getTheme: () => ThemePreference;
  readonly setTheme: (theme: ThemePreference) => void;
  readonly saveTheme: (theme: ThemePreference) => Promise<void>;
}

export type LocalCommandResult =
  | { readonly kind: "help"; readonly command?: string }
  | { readonly kind: "picker"; readonly selection: PickerSelection }
  | { readonly kind: "notice"; readonly message: string }
  | { readonly kind: "warning"; readonly message: string }
  | { readonly kind: "shutdown" };

export class LocalCommandService {
  constructor(private readonly dependencies: LocalCommandDependencies) {}

  async execute(intent: LocalCommandIntent): Promise<LocalCommandResult> {
    switch (intent.name) {
      case "help":
        return this.#help(intent.arguments ?? []);
      case "theme":
        return await this.#theme(intent.arguments ?? []);
      case "copy":
        return await this.#copy(intent.arguments ?? []);
      case "quit":
        return intent.arguments?.length
          ? { kind: "warning", message: "Usage: /quit" }
          : { kind: "shutdown" };
    }
  }

  #help(arguments_: readonly string[]): LocalCommandResult {
    if (arguments_.length === 0) return { kind: "help" };
    if (arguments_.length !== 1) {
      return { kind: "warning", message: "Usage: /help [command]" };
    }
    const command = arguments_[0]?.replace(/^\//u, "") ?? "";
    return findCommand(command)
      ? { kind: "help", command }
      : { kind: "warning", message: `No command named /${command}.` };
  }

  async #theme(arguments_: readonly string[]): Promise<LocalCommandResult> {
    if (arguments_.length === 0) {
      const current = this.dependencies.getTheme();
      return {
        kind: "picker",
        selection: {
          prompt: "Theme",
          options: (["system", "dark", "light"] as const).map((value) => ({
            value,
            label: value[0]?.toUpperCase() + value.slice(1),
            selected: value === current,
          })),
        },
      };
    }
    const selected = arguments_[0];
    if (
      arguments_.length !== 1 ||
      (selected !== "system" && selected !== "dark" && selected !== "light")
    ) {
      return {
        kind: "warning",
        message: "Usage: /theme [system|dark|light]",
      };
    }
    await this.dependencies.saveTheme(selected);
    this.dependencies.setTheme(selected);
    return { kind: "notice", message: `Theme changed to ${selected}.` };
  }

  async #copy(arguments_: readonly string[]): Promise<LocalCommandResult> {
    if (arguments_.length > 0) {
      return { kind: "warning", message: "Usage: /copy" };
    }
    const answer = latestAssistantAnswer(this.dependencies.getThread());
    if (answer === undefined) {
      return {
        kind: "warning",
        message: "No durable Assistant answer is available to copy.",
      };
    }
    try {
      await this.dependencies.clipboard.writeText(answer);
      return {
        kind: "notice",
        message: "Copied latest Assistant answer.",
      };
    } catch {
      return { kind: "warning", message: "Clipboard is unavailable." };
    }
  }
}

export function latestAssistantAnswer(
  thread: MethodValue["thread.read"] | undefined,
): string | undefined {
  return thread?.view.entries
    .filter((entry) => entry.kind === "assistant_message")
    .toSorted((left, right) => right.sequence - left.sequence)[0]?.content;
}
