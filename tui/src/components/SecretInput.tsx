import { Box, Text } from "ink";

import { graphemes } from "../composer/graphemes.js";
import { useTheme } from "./theme.js";

export interface SecretInputProps {
  readonly label: string;
  readonly value: string;
  readonly submitting?: boolean;
  readonly message?: string;
}

export function SecretInput({
  label,
  value,
  submitting = false,
  message,
}: SecretInputProps) {
  const theme = useTheme();
  return (
    <Box flexDirection="column">
      <Text color={theme.primary}>{label}</Text>
      <Text>{"•".repeat(graphemes(value).length)}</Text>
      <Text dimColor>
        {submitting ? "Saving…" : "Enter to save · Esc to cancel"}
      </Text>
      {message ? <Text color={theme.warning}>{message}</Text> : null}
    </Box>
  );
}
