import { COMMAND_CATALOG, type CommandMetadata } from "./catalog.js";

export function searchCommands(query: string): readonly CommandMetadata[] {
  const normalized = query.trim().replace(/^\//u, "").toLocaleLowerCase();
  return COMMAND_CATALOG.map((command, index) => ({
    command,
    index,
    rank: matchRank(command, normalized),
  }))
    .filter(({ rank }) => rank < 3)
    .sort((left, right) => left.rank - right.rank || left.index - right.index)
    .map(({ command }) => command);
}

function matchRank(command: CommandMetadata, query: string): number {
  if (query.length === 0) return 1;
  if (command.name === query) return 0;
  if (command.name.startsWith(query)) return 1;
  if (command.description.toLocaleLowerCase().includes(query)) return 2;
  return 3;
}
