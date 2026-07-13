import { Box, Text } from "ink";

import type { PickerSelection } from "../../interaction/model.js";
import { useTheme } from "../theme.js";

export function SelectionRows({
  options,
  selected,
  accent,
  sectionForOption,
}: {
  readonly options: PickerSelection["options"];
  readonly selected: number;
  readonly accent: string;
  readonly sectionForOption?:
    | ((value: string) => string | undefined)
    | undefined;
}) {
  const theme = useTheme();
  return options.map((option, index) => {
    const section = sectionForOption?.(option.value);
    return (
      <Box key={option.value} flexDirection="column">
        {section ? <Text color={theme.muted}>{section}</Text> : null}
        <Text
          {...(option.disabled
            ? { color: theme.muted, dimColor: true }
            : index === selected
              ? { color: accent }
              : {})}
        >
          {index === selected ? "› " : "  "}
          {option.label}
          {option.description ? ` · ${option.description}` : ""}
        </Text>
      </Box>
    );
  });
}
