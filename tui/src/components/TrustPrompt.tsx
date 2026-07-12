import { Box, Text } from "ink";

import { useTheme } from "./theme.js";

export function TrustPrompt({
  workspacePath,
  selected,
  submitting = false,
  message,
}: {
  readonly workspacePath: string;
  readonly selected: number;
  readonly submitting?: boolean;
  readonly message?: string;
}) {
  const theme = useTheme();
  return (
    <Box flexDirection="column">
      <Text bold color={theme.brand}>
        Trust this workspace?
      </Text>
      <Text> </Text>
      <Text color={theme.brand}>{workspacePath}</Text>
      <Text> </Text>
      <Text>Is this a project you created or trust?</Text>
      <Text color={theme.muted}>
        Awesome can read files in this workspace. File changes and shell
        commands
      </Text>
      <Text color={theme.muted}>follow your current permission mode.</Text>
      <Text> </Text>
      <TrustChoice
        active={selected === 0}
        label="1. Yes, I trust this folder"
      />
      <TrustChoice active={selected === 1} label="2. No, exit" />
      <Text> </Text>
      <Text color={theme.muted}>
        {submitting ? "Saving trust…" : "↑/↓ Select · Enter Confirm · Esc Exit"}
      </Text>
      {message ? <Text color={theme.error}>{message}</Text> : null}
    </Box>
  );
}

function TrustChoice({ active, label }: { active: boolean; label: string }) {
  const theme = useTheme();
  return (
    <Text {...(active ? { color: theme.brand } : {})}>
      {active ? "❯ " : "  "}
      {label}
    </Text>
  );
}
