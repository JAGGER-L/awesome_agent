import { Box, Text } from "ink";

import type { CommandMetadata } from "../commands/catalog.js";
import { commandMenuWindow } from "../commands/menu-window.js";
import type { CommandName } from "../protocol/commands.js";
import { useTheme } from "./theme.js";

export function CommandMenu({
  commands,
  selectedCommand,
  viewportStart,
}: {
  readonly commands: readonly CommandMetadata[];
  readonly selectedCommand?: CommandName;
  readonly viewportStart: number;
}) {
  const theme = useTheme();
  if (commands.length === 0) {
    return <Text color={theme.muted}>No matching commands</Text>;
  }
  const window = commandMenuWindow(commands, selectedCommand, viewportStart);
  return (
    <Box flexDirection="column">
      {window.items.map((command) => {
        const selected = command.name === selectedCommand;
        return (
          <Text key={command.name}>
            <Text color={selected ? theme.primary : theme.muted}>
              {selected ? "› " : "  "}
              {command.completion}
            </Text>
            <Text color={theme.muted}> — {command.description}</Text>
          </Text>
        );
      })}
      <Text color={theme.muted}>
        {window.start + 1}–{window.end} of {window.total}
      </Text>
    </Box>
  );
}
