import { Box, Text } from "ink";

import { Picker } from "./Picker.js";
import { useTheme } from "./theme.js";

export function TrustPrompt({
  workspacePath,
  selected,
}: {
  readonly workspacePath: string;
  readonly selected: number;
}) {
  const theme = useTheme();
  return (
    <Box flexDirection="column">
      <Text color={theme.warning}>Trust this workspace?</Text>
      <Text>{workspacePath}</Text>
      <Picker
        selected={selected}
        selection={{
          prompt: "Choose explicitly",
          options: [
            { value: "trust", label: "Trust workspace", selected: true },
            { value: "deny", label: "Deny and exit", selected: false },
          ],
        }}
      />
    </Box>
  );
}
