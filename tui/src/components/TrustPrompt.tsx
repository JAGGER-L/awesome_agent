import { Box, Text } from "ink";

import { Picker } from "./Picker.js";
import { useTheme } from "./theme.js";

export function TrustPrompt({
  workspacePath,
  onDecision,
}: {
  readonly workspacePath: string;
  readonly onDecision: (decision: "trust" | "deny") => void;
}) {
  const theme = useTheme();
  return (
    <Box flexDirection="column">
      <Text color={theme.warning}>Trust this workspace?</Text>
      <Text>{workspacePath}</Text>
      <Picker
        blocking
        selection={{
          prompt: "Choose explicitly",
          options: [
            { value: "trust", label: "Trust workspace", selected: true },
            { value: "deny", label: "Deny and exit", selected: false },
          ],
        }}
        onSelect={(value) => onDecision(value === "trust" ? "trust" : "deny")}
        onClose={() => {}}
      />
    </Box>
  );
}
