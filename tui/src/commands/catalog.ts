import {
  commandNames,
  commandOwners,
  type CommandName,
  type CommandOwner,
} from "../protocol/commands.js";

export interface CommandMetadata {
  readonly name: CommandName;
  readonly owner: CommandOwner;
  readonly usage: string;
  readonly description: string;
  readonly examples: readonly string[];
}

const metadata: Readonly<Record<CommandName, readonly [string, string]>> = {
  new: ["/new", "Start a new thread"],
  resume: ["/resume [thread_id]", "Resume a previous thread"],
  context: ["/context", "Show the active context"],
  compact: ["/compact", "Compact the current context"],
  model: ["/model [provider]", "Choose a Provider and model"],
  auth: ["/auth [provider]", "Manage Provider API keys"],
  thinking: ["/thinking [on|off]", "Show or choose thinking mode"],
  workspace: ["/workspace", "Show workspace trust details"],
  diff: ["/diff", "Show workspace changes"],
  undo: ["/undo", "Undo the latest reversible change"],
  redo: ["/redo", "Redo the latest undone change"],
  tools: ["/tools", "List available tools"],
  skills: ["/skills", "List available skills"],
  skill: ["/skill [name]", "Show or choose the active skill"],
  mcp: ["/mcp", "Show MCP server status"],
  memory: ["/memory", "Show or configure memory"],
  status: ["/status", "Show current agent status"],
  usage: ["/usage", "Show token and operation usage"],
  doctor: ["/doctor", "Run local diagnostics"],
  config: ["/config", "Show effective configuration"],
  permissions: ["/permissions", "Show or choose permission mode"],
  init: ["/init", "Initialize repository instructions"],
  review: ["/review", "Review the current code changes"],
  debug: ["/debug", "Investigate a defect"],
  test: ["/test", "Run focused validation"],
  commit: ["/commit", "Prepare a focused commit"],
  help: ["/help [command]", "Show command help"],
  theme: ["/theme [system|dark|light]", "Show or choose the color theme"],
  copy: ["/copy", "Copy the latest assistant answer to the clipboard"],
  quit: ["/quit", "Quit Awesome"],
};

export const COMMAND_CATALOG: readonly CommandMetadata[] = commandNames.map(
  (name) => ({
    name,
    owner: commandOwners[name],
    usage: metadata[name][0],
    description: metadata[name][1],
    examples: [metadata[name][0]],
  }),
);

export function findCommand(name: string): CommandMetadata | undefined {
  return COMMAND_CATALOG.find((command) => command.name === name);
}
