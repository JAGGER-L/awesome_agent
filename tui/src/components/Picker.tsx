import { Box, Text, useInput } from "ink";
import { useRef, useState } from "react";

import { useTheme } from "./theme.js";

export interface PickerSelection {
  readonly prompt: string;
  readonly options: readonly {
    readonly value: string;
    readonly label: string;
    readonly description?: string | undefined;
    readonly selected: boolean;
  }[];
}

export function Picker({
  selection,
  onSelect,
  onClose,
  blocking = false,
  active = true,
}: {
  readonly selection: PickerSelection;
  readonly onSelect: (value: string) => void;
  readonly onClose: () => void;
  readonly blocking?: boolean;
  readonly active?: boolean;
}) {
  const initial = Math.max(
    0,
    selection.options.findIndex((option) => option.selected),
  );
  const [selected, setSelected] = useState(initial);
  const selectedRef = useRef(initial);
  const theme = useTheme();

  useInput(
    (_input, key) => {
      if (key.upArrow) {
        selectedRef.current =
          selectedRef.current <= 0
            ? selection.options.length - 1
            : selectedRef.current - 1;
        setSelected(selectedRef.current);
      } else if (key.downArrow) {
        selectedRef.current =
          (selectedRef.current + 1) % selection.options.length;
        setSelected(selectedRef.current);
      } else if (key.return) {
        const option = selection.options[selectedRef.current];
        if (option) onSelect(option.value);
      } else if (key.escape && !blocking) {
        onClose();
      }
    },
    { isActive: active },
  );

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
