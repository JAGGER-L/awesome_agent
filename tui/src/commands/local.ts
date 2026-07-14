import type { ClipboardAdapter } from "../adapters/clipboard.js";
import type { PickerSelection } from "../interaction/model.js";
import type { MethodValue } from "../protocol/methods.js";
import type { ThemePreference } from "../preferences/theme.js";
import { helpForCommand, helpOverview, type HelpResult } from "./help.js";
import type { LocalCommandIntent } from "./parser.js";

export interface LocalCommandDependencies {
  readonly clipboard: ClipboardAdapter;
  readonly getThread: () => MethodValue["thread.read"] | undefined;
  readonly getTheme: () => ThemePreference;
  readonly setTheme: (theme: ThemePreference) => void;
  readonly saveTheme: (theme: ThemePreference) => Promise<void>;
}

export type LocalCommandResult =
  | HelpResult
  | {
      readonly kind: "result";
      readonly command: string;
      readonly tone: "info" | "warning" | "error";
      readonly content: string;
    }
  | { readonly kind: "picker"; readonly selection: PickerSelection }
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
          ? {
              kind: "result",
              command: "quit",
              tone: "warning",
              content: "Usage: /quit",
            }
          : { kind: "shutdown" };
    }
  }

  #help(arguments_: readonly string[]): LocalCommandResult {
    if (arguments_.length === 0) {
      return helpOverview();
    }
    if (arguments_.length !== 1) {
      return {
        kind: "result",
        command: "help",
        tone: "warning",
        content: "Usage: /help [command]",
      };
    }
    const command = arguments_[0]?.replace(/^\//u, "") ?? "";
    const help = helpForCommand(command);
    return help
      ? help
      : {
          kind: "result",
          command: "help",
          tone: "warning",
          content: `No command named /${command}.`,
        };
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
        kind: "result",
        command: "theme",
        tone: "warning",
        content: "Usage: /theme [system|dark|light]",
      };
    }
    await this.dependencies.saveTheme(selected);
    this.dependencies.setTheme(selected);
    return {
      kind: "result",
      command: "theme",
      tone: "info",
      content: `Theme changed to ${selected}.`,
    };
  }

  async #copy(arguments_: readonly string[]): Promise<LocalCommandResult> {
    if (arguments_.length > 0) {
      return {
        kind: "result",
        command: "copy",
        tone: "warning",
        content: "Usage: /copy",
      };
    }
    const answer = latestAssistantAnswer(this.dependencies.getThread());
    if (answer === undefined) {
      return {
        kind: "result",
        command: "copy",
        tone: "warning",
        content: "No durable Assistant answer is available to copy.",
      };
    }
    try {
      await this.dependencies.clipboard.writeText(answer);
      return {
        kind: "result",
        command: "copy",
        tone: "info",
        content: "Copied latest Assistant answer.",
      };
    } catch {
      return {
        kind: "result",
        command: "copy",
        tone: "warning",
        content: "Clipboard is unavailable.",
      };
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
