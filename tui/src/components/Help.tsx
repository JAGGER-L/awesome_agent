import { Box, Text } from "ink";

import { COMMAND_CATALOG, findCommand } from "../commands/catalog.js";
import type { CommandOwner } from "../protocol/commands.js";
import { useTheme } from "./theme.js";

const groups: readonly [CommandOwner, string][] = [
  ["application", "Application"],
  ["skill", "Skills"],
  ["ink", "Ink local"],
];

export function Help({ command }: { readonly command?: string }) {
  const theme = useTheme();
  if (command) {
    const metadata = findCommand(command);
    if (!metadata) {
      return <Text color={theme.warning}>No command named /{command}.</Text>;
    }
    return (
      <Box flexDirection="column">
        <Text color={theme.accent}>{metadata.usage}</Text>
        <Text>Owner: {metadata.owner}</Text>
        <Text>{metadata.description}</Text>
        <Text>Examples</Text>
        {metadata.examples.map((example) => (
          <Text key={example}> {example}</Text>
        ))}
      </Box>
    );
  }

  return (
    <Box flexDirection="column">
      <Text color={theme.accent}>Commands</Text>
      {groups.map(([owner, label]) => (
        <Box key={owner} flexDirection="column">
          <Text>{label}</Text>
          <Text>
            {COMMAND_CATALOG.filter((item) => item.owner === owner)
              .map((item) => `/${item.name}`)
              .join(" · ")}
          </Text>
        </Box>
      ))}
    </Box>
  );
}
