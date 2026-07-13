import { Box, Text } from "ink";
import type { ReactNode } from "react";

import type { PickerSelection } from "../../interaction/model.js";
import { useTheme } from "../theme.js";
import { SelectionRows } from "./SelectionRows.js";

export type SelectionVariant = "neutral" | "brand" | "warning" | "danger";

export function SelectionPanel({
  title,
  options,
  selected,
  variant = "neutral",
  width,
  submitting = false,
  message,
  sectionForOption,
  children,
}: {
  readonly title: string;
  readonly options: PickerSelection["options"];
  readonly selected: number;
  readonly variant?: SelectionVariant;
  readonly width?: number | undefined;
  readonly submitting?: boolean;
  readonly message?: string | undefined;
  readonly sectionForOption?:
    | ((value: string) => string | undefined)
    | undefined;
  readonly children?: ReactNode;
}) {
  const theme = useTheme();
  const accent =
    variant === "brand"
      ? theme.brand
      : variant === "warning"
        ? theme.warning
        : variant === "danger"
          ? theme.danger
          : theme.primary;
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={accent}
      paddingX={1}
      {...(width === undefined ? {} : { width })}
    >
      <Text bold color={accent}>
        {title}
      </Text>
      {children}
      <SelectionRows
        options={options}
        selected={selected}
        accent={accent}
        sectionForOption={sectionForOption}
      />
      <Text color={theme.muted}>
        {submitting ? "Submitting…" : "↑↓ select · Enter confirm · Esc cancel"}
      </Text>
      {message ? <Text color={theme.danger}>{message}</Text> : null}
    </Box>
  );
}
