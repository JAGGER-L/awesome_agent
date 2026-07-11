import type { CommandName } from "../protocol/commands.js";
import { findCommand } from "./catalog.js";

export interface CommandIntent {
  readonly name: CommandName;
  readonly arguments?: readonly string[];
}

export type LocalCommandName = "help" | "theme" | "copy" | "quit";

export interface LocalCommandIntent {
  readonly name: LocalCommandName;
  readonly arguments?: readonly string[];
}

export type RoutedInput =
  | { readonly kind: "turn"; readonly content: string }
  | { readonly kind: "direct"; readonly command: string }
  | { readonly kind: "command"; readonly intent: CommandIntent }
  | { readonly kind: "local"; readonly intent: LocalCommandIntent };

export type ParseFailure = {
  readonly kind: "invalid";
  readonly code: "unknown_command" | "invalid_arguments";
};

export function parseInput(
  input: string,
): RoutedInput | ParseFailure | undefined {
  if (input.trim().length === 0) return undefined;
  const classified = input.trimStart();
  if (classified.startsWith("!")) {
    const command = classified.slice(1);
    return command.trim().length === 0
      ? { kind: "invalid", code: "invalid_arguments" }
      : { kind: "direct", command };
  }
  if (!classified.startsWith("/")) return { kind: "turn", content: input };

  const tokens = tokenize(classified.slice(1));
  if (!tokens) return { kind: "invalid", code: "invalid_arguments" };
  const [name, ...args] = tokens;
  const command = findCommand(name ?? "");
  if (!command) return { kind: "invalid", code: "unknown_command" };
  const intent = {
    name: command.name,
    ...(args.length > 0 ? { arguments: args } : {}),
  };
  return command.owner === "ink"
    ? { kind: "local", intent: intent as LocalCommandIntent }
    : { kind: "command", intent };
}

function tokenize(value: string): string[] | undefined {
  const tokens: string[] = [];
  let token = "";
  let quote: "'" | '"' | undefined;
  let escaped = false;
  let started = false;

  for (const character of value) {
    if (escaped) {
      token += character;
      escaped = false;
      started = true;
      continue;
    }
    if (character === "\\") {
      escaped = true;
      started = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = undefined;
      else token += character;
      started = true;
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      started = true;
      continue;
    }
    if (/\s/u.test(character)) {
      if (started) {
        tokens.push(token);
        token = "";
        started = false;
      }
      continue;
    }
    token += character;
    started = true;
  }
  if (escaped || quote) return undefined;
  if (started) tokens.push(token);
  return tokens;
}
