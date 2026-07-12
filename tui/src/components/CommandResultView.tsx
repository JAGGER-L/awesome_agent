import { Box, Text } from "ink";

import type { CommandPresentation } from "../commands/presenters.js";
import { useTheme } from "./theme.js";

export function CommandResultView({
  presentation,
  width,
}: {
  readonly presentation: CommandPresentation;
  readonly width: number;
}) {
  const theme = useTheme();
  const color =
    presentation.kind === "error" ||
    ("tone" in presentation && presentation.tone === "error")
      ? theme.danger
      : "tone" in presentation && presentation.tone === "warning"
        ? theme.warning
        : theme.muted;
  if (presentation.kind === "error") {
    return (
      <Box flexDirection="column">
        <Text color={color}>{presentation.title}</Text>
        <Text color={theme.danger}>{presentation.message}</Text>
      </Box>
    );
  }
  if (presentation.kind === "progress") {
    return <Text color={color}>{presentation.message}</Text>;
  }
  if (presentation.kind === "picker" || presentation.kind === "secret")
    return null;
  const labelWidth = Math.min(
    28,
    Math.max(0, ...presentation.rows.map((row) => row.label.length)),
  );
  return (
    <Box flexDirection="column" width={width}>
      <Text color={color}>{presentation.title}</Text>
      {presentation.rows.map((row) => (
        <Box key={`${row.label}\u0000${row.value}`}>
          {labelWidth > 0 ? (
            <Box width={labelWidth + 2}>
              <Text>{row.label}</Text>
            </Box>
          ) : null}
          <Text>{row.value}</Text>
        </Box>
      ))}
    </Box>
  );
}
