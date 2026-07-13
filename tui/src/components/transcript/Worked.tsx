import { Box, Text } from "ink";

import { formatDuration } from "../../transcript/reasoning.js";
import { useTheme } from "../theme.js";

export function Worked({ durationMs }: { readonly durationMs: number }) {
  const theme = useTheme();
  if (!theme.colorEnabled) {
    return (
      <Box marginTop={1}>
        <Text>[Worked] {formatDuration(durationMs)}</Text>
      </Box>
    );
  }
  const background = theme.statusBackground ?? theme.border;
  return (
    <Box marginTop={1}>
      <Text
        bold
        color={theme.secondary}
        backgroundColor={background}
      >
        ✻
      </Text>
      <Text color={theme.muted} backgroundColor={background}>
        {` Worked for ${formatDuration(durationMs)} `}
      </Text>
    </Box>
  );
}
