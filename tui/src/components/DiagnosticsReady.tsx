import { Box, Text } from "ink";

import { useTheme } from "./theme.js";

const safeCommands = [
  "/config",
  "/doctor",
  "/model",
  "/workspace",
  "/help",
  "/quit",
] as const;

export function DiagnosticsReady({
  model,
  environmentVariable,
  diagnostics,
}: {
  readonly model: string;
  readonly environmentVariable?: string;
  readonly diagnostics: readonly string[];
}) {
  const theme = useTheme();
  return (
    <Box flexDirection="column">
      <Text color={theme.warning}>Agent turns are not ready for {model}.</Text>
      {environmentVariable ? (
        <Text>Set {environmentVariable} in the environment, then restart.</Text>
      ) : null}
      {diagnostics.map((diagnostic) => (
        <Text key={diagnostic} color={theme.error}>
          {diagnostic}
        </Text>
      ))}
      <Text>Safe commands: {safeCommands.join(" · ")}</Text>
    </Box>
  );
}
