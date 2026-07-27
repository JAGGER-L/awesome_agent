import {
  commandNames,
  commandOwners,
  type CommandName,
  type CommandOwner,
} from "../protocol/commands.js";

export interface CommandMetadata {
  readonly name: CommandName;
  readonly owner: CommandOwner;
  readonly completion: `/${CommandName}`;
  readonly usage: string;
  readonly description: string;
  readonly examples: readonly string[];
}

const metadata: Readonly<
  Record<CommandName, Omit<CommandMetadata, "name" | "owner">>
> = {
  new: entry("new", "/new", "Start a new thread"),
  rename: entry("rename", "/rename <title>", "Rename the current conversation"),
  resume: entry("resume", "/resume [thread_id]", "Resume a previous thread"),
  search: entry(
    "search",
    "/search <query> [thread_id]",
    "Search conversations in this workspace; quote multi-word queries",
    ['/search "provider retry"', '/search "provider retry" thread_123'],
  ),
  context: entry("context", "/context", "Show the active context"),
  compact: entry("compact", "/compact", "Compact the current context"),
  model: entry("model", "/model [provider]", "Choose a Provider and model"),
  auth: entry("auth", "/auth [provider]", "Manage Provider API keys"),
  thinking: entry(
    "thinking",
    "/thinking [on|off]",
    "Show or choose thinking mode",
  ),
  workspace: entry(
    "workspace",
    "/workspace",
    "Show the current workspace path",
  ),
  diff: entry("diff", "/diff", "Show workspace changes"),
  export: entry(
    "export",
    "/export <workspace-relative-path> [markdown|json]",
    "Export the current conversation",
    ["/export reports/thread.md", "/export reports/thread.json json"],
  ),
  undo: entry("undo", "/undo", "Undo the latest reversible change"),
  redo: entry("redo", "/redo", "Redo the latest undone change"),
  tools: entry("tools", "/tools", "List available tools"),
  web: entry(
    "web",
    "/web [on|off|status|revoke]",
    "Show or configure Tavily Web access",
  ),
  skills: entry(
    "skills",
    "/skills [auto|off|name]",
    "Show or choose the active skill",
  ),
  mcp: entry("mcp", "/mcp", "Show MCP server status"),
  memory: entry("memory", "/memory", "Show or configure memory"),
  status: entry("status", "/status", "Show current agent status"),
  usage: entry("usage", "/usage", "Show token and operation usage"),
  doctor: entry("doctor", "/doctor", "Run local diagnostics"),
  config: entry("config", "/config", "Show effective configuration"),
  permissions: entry(
    "permissions",
    "/permissions",
    "Show or choose permission mode",
  ),
  help: entry("help", "/help [command]", "Show command help"),
  theme: entry(
    "theme",
    "/theme [system|dark|light]",
    "Show or choose the color theme",
  ),
  copy: entry(
    "copy",
    "/copy",
    "Copy the latest assistant answer to the clipboard",
  ),
  quit: entry("quit", "/quit", "Quit Awesome"),
};

function entry<Name extends CommandName>(
  name: Name,
  usage: string,
  description: string,
  examples: readonly string[] = [usage],
): Omit<CommandMetadata, "name" | "owner"> {
  return {
    completion: `/${name}`,
    usage,
    description,
    examples,
  };
}

export const COMMAND_CATALOG: readonly CommandMetadata[] = commandNames.map(
  (name) => ({
    name,
    owner: commandOwners[name],
    ...metadata[name],
  }),
);

export function findCommand(name: string): CommandMetadata | undefined {
  return COMMAND_CATALOG.find((command) => command.name === name);
}
