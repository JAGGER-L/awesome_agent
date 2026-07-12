import { Box, Text } from "ink";

import type { CommandMetadata } from "../commands/catalog.js";
import type { CommandName } from "../protocol/commands.js";
import { useTheme } from "./theme.js";

export function CommandMenu({
  commands,
  selectedCommand,
}: {
  readonly commands: readonly CommandMetadata[];
  readonly selectedCommand?: CommandName;
}) {
  const theme = useTheme();
  if (commands.length === 0) return null;
  return (
    <Box flexDirection="column">
      {commands.map((command) => {
        const selected = command.name === selectedCommand;
        return (
          <Text key={command.name}>
            <Text color={selected ? theme.primary : theme.muted}>
              {selected ? "› " : "  "}
              {command.usage}
            </Text>
            <Text color={theme.muted}> — {command.description}</Text>
          </Text>
        );
      })}
    </Box>
  );
}
