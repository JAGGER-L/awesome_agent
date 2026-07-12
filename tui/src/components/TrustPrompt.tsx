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
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.brand}
      paddingX={1}
    >
      <Text bold color={theme.brand}>
        Trust this workspace?
      </Text>
      <Text> </Text>
      <Text color={theme.brand}>{workspacePath}</Text>
      <Text> </Text>
      <Text>
        Quick safety check: Is this a project you created or one you trust?
      </Text>
      <Text color={theme.muted}>
        Awesome can read, edit, and execute files in this workspace. Review
        unfamiliar projects before continuing.
      </Text>
      <Text> </Text>
      <TrustChoice
        active={selected === 0}
        label="1. Yes, I trust this folder"
      />
      <TrustChoice active={selected === 1} label="2. No, exit" />
      <Text> </Text>
      <Text color={theme.muted}>
        {submitting
          ? "Saving trust…"
          : "↑/↓ select · Enter confirm · Esc cancel"}
      </Text>
      {message ? <Text color={theme.danger}>{message}</Text> : null}
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
