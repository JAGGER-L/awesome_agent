import { Box, Text } from "ink";

import type { PickerSelection } from "../interaction/model.js";
import { useTheme } from "./theme.js";

export function AuthPicker({
  selection,
  selected,
  width,
}: {
  readonly selection: NonNullable<PickerSelection>;
  readonly selected: number;
  readonly width?: number;
}) {
  const theme = useTheme();
  const services = selection.options.some((option) => option.value === "mem0");
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.secondary}
      paddingX={1}
      {...(width === undefined ? {} : { width })}
    >
      <Text bold>{selection.prompt}</Text>
      {selection.options.map((option, index) => (
        <Box key={option.value} flexDirection="column">
          {services && option.value === "deepseek" ? (
            <Text color={theme.muted}>Model providers</Text>
          ) : null}
          {services && option.value === "mem0" ? (
            <Text color={theme.muted}>Memory providers</Text>
          ) : null}
          <Text
            {...(option.disabled
              ? { color: theme.muted }
              : index === selected
                ? { color: theme.secondary }
                : {})}
          >
            {index === selected ? "›" : " "} {option.label}
            {option.description ? ` · ${option.description}` : ""}
          </Text>
        </Box>
      ))}
      <Text color={theme.muted}>↑/↓ select · Enter confirm · Esc cancel</Text>
    </Box>
  );
}
