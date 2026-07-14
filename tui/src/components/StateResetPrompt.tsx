import { Text } from "ink";

import { SelectionPanel } from "./interactions/index.js";
import { useTheme } from "./theme.js";

const resetOptions = [
  {
    value: "reset_state",
    label: "Reset local state and continue",
    selected: true,
    disabled: false,
  },
  { value: "deny", label: "Exit", selected: false, disabled: false },
] as const;

export function StateResetPrompt({
  selected,
  submitting = false,
  message,
}: {
  readonly selected: number;
  readonly submitting?: boolean;
  readonly message?: string | undefined;
}) {
  const theme = useTheme();
  return (
    <SelectionPanel
      title="Awesome needs to reset local state"
      options={resetOptions}
      selected={selected}
      variant="danger"
      submitting={submitting}
      message={message}
    >
      <Text> </Text>
      <Text>This version uses a new local data format.</Text>
      <Text> </Text>
      <Text color={theme.warning}>Resetting removes:</Text>
      <Text> • Conversations and threads</Text>
      <Text> • Workspace trust</Text>
      <Text> • Checkpoints and undo history</Text>
      <Text> </Text>
      <Text color={theme.success}>The following are kept:</Text>
      <Text> • API keys and configuration</Text>
      <Text> • Skills</Text>
      <Text> • Local and Cloud Memory settings</Text>
      <Text> </Text>
    </SelectionPanel>
  );
}
