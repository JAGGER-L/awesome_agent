import { Box, Text } from "ink";

import { searchCommands } from "../commands/search.js";
import { useTheme } from "./theme.js";

export function CommandMenu({ query }: { readonly query: string }) {
  const classified = query.trimStart();
  const theme = useTheme();
  if (!classified.startsWith("/") || /\s/u.test(classified)) return null;
  const matches = searchCommands(classified);
  if (matches.length === 0) return null;
  return (
    <Box flexDirection="column">
      {matches.map((command) => (
        <Text key={command.name}>
          <Text color={theme.accent}>{command.usage}</Text>
          <Text color={theme.muted}> — {command.description}</Text>
        </Text>
      ))}
    </Box>
  );
}
