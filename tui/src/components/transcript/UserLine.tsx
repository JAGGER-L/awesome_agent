import { Box, Text } from "ink";

import { useTheme } from "../theme.js";

export function UserLine({
  text,
  failure,
}: {
  readonly text: string;
  readonly failure?: string;
}) {
  const theme = useTheme();
  return (
    <Box flexDirection="column">
      <Text color={theme.user}>❯ {text}</Text>
      {failure ? <Text color={theme.danger}>Failed · {failure}</Text> : null}
    </Box>
  );
}
