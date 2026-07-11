import { Box, Text } from "ink";

import { useTheme } from "./theme.js";

export function ProviderSetupNotice() {
  const theme = useTheme();
  return (
    <Box flexDirection="column">
      <Text color={theme.accent}>Choose a model Provider to get started.</Text>
      <Text>
        Press Enter or run /model. You can manage API keys with /auth.
      </Text>
    </Box>
  );
}
