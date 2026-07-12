import { Box, Text } from "ink";

import type { PickerSelection } from "../interaction/model.js";
import { useTheme } from "./theme.js";

export function Picker({
  selection,
  selected,
}: {
  readonly selection: PickerSelection;
  readonly selected: number;
}) {
  const theme = useTheme();
  return (
    <Box flexDirection="column">
      <Text color={theme.accent}>{selection.prompt}</Text>
      {selection.options.map((option, index) => (
        <Text
          key={option.value}
          {...(index === selected ? { color: theme.accent } : {})}
        >
          {index === selected ? "› " : "  "}
          {option.label}
          {option.description ? ` — ${option.description}` : ""}
        </Text>
      ))}
    </Box>
  );
}
