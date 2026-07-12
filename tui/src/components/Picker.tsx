import { Box, Text } from "ink";

import type { PickerSelection } from "../interaction/model.js";
import { useTheme } from "./theme.js";

export function Picker({
  selection,
  selected,
  variant = "neutral",
}: {
  readonly selection: PickerSelection;
  readonly selected: number;
  readonly variant?: "neutral" | "warning" | "danger";
}) {
  const theme = useTheme();
  const accent =
    variant === "danger"
      ? theme.danger
      : variant === "warning"
        ? theme.warning
        : theme.primary;
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={accent}
      paddingX={1}
    >
      <Text bold color={accent}>
        {selection.prompt}
      </Text>
      {selection.options.map((option, index) => (
        <Text
          key={option.value}
          {...(option.disabled
            ? { color: theme.muted }
            : index === selected
              ? { color: accent }
              : {})}
        >
          {index === selected ? "› " : "  "}
          {option.label}
          {option.description ? ` — ${option.description}` : ""}
        </Text>
      ))}
      <Text color={theme.muted}>↑/↓ select · Enter confirm · Esc cancel</Text>
    </Box>
  );
}
