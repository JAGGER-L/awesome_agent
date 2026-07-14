import { Text } from "ink";

import { SelectionPanel } from "./interactions/index.js";
import { useTheme } from "./theme.js";

const trustOptions = [
  {
    value: "trust",
    label: "1. Yes, I trust this folder",
    selected: true,
    disabled: false,
  },
  { value: "exit", label: "2. No, exit", selected: false, disabled: false },
] as const;

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
    <SelectionPanel
      title="Trust this workspace?"
      options={trustOptions}
      selected={selected}
      variant="brand"
      submitting={submitting}
      message={message}
    >
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
    </SelectionPanel>
  );
}
