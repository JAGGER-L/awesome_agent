import { COMMAND_CATALOG, findCommand } from "./catalog.js";

export interface HelpRow {
  readonly usage: string;
  readonly description: string;
}

export interface HelpResult {
  readonly kind: "help";
  readonly rows: readonly HelpRow[];
}

export function helpOverview(): HelpResult {
  return {
    kind: "help",
    rows: COMMAND_CATALOG.map(({ usage, description }) => ({
      usage,
      description,
    })),
  };
}

export function helpForCommand(name: string): HelpResult | undefined {
  const command = findCommand(name.replace(/^\//u, ""));
  return command
    ? {
        kind: "help",
        rows: [{ usage: command.usage, description: command.description }],
      }
    : undefined;
}
