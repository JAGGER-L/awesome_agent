import { Box, Text } from "ink";
import type { ReactNode } from "react";

import { useTheme } from "../theme.js";

export function ExpandableDetails({
  summary,
  expanded,
  children,
}: {
  readonly summary: ReactNode;
  readonly expanded: boolean;
  readonly children: ReactNode;
}) {
  const theme = useTheme();
  return (
    <Box flexDirection="column">
      <Box>
        {summary}
        <Text color={theme.muted}>
          {expanded ? " · Ctrl+O to collapse" : " · Ctrl+O to expand"}
        </Text>
      </Box>
      {expanded ? children : null}
    </Box>
  );
}
